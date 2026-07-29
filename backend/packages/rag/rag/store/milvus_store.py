"""Milvus 向量库：dense + sparse hybrid，跨进程共享检索索引。"""

from __future__ import annotations

import json
import asyncio
from typing import Any

from shared.config import settings
from shared.logging import get_logger

from rag.models import Chunk, RetrievalHit

logger = get_logger(__name__)

DEFAULT_DIM = 1536


class MilvusVectorStore:
    """封装 pymilvus：dense HNSW + sparse inverted index。"""

    def __init__(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        collection: str | None = None,
        dim: int | None = None,
    ) -> None:
        self.host = host or settings.milvus_host
        self.port = port or settings.milvus_port
        self.collection_name = collection or settings.milvus_collection_v2
        self.dim = dim or settings.milvus_dim or DEFAULT_DIM
        self._connected = False
        self._collection: Any = None

    def connect(self) -> None:
        from pymilvus import (
            Collection,
            CollectionSchema,
            DataType,
            FieldSchema,
            connections,
            utility,
        )

        connections.connect(alias="default", host=self.host, port=str(self.port))
        if not utility.has_collection(self.collection_name):
            fields = [
                FieldSchema(name="chunk_id", dtype=DataType.VARCHAR, is_primary=True, max_length=64),
                FieldSchema(name="doc_id", dtype=DataType.VARCHAR, max_length=64),
                FieldSchema(name="tenant_id", dtype=DataType.VARCHAR, max_length=64),
                FieldSchema(name="kb_id", dtype=DataType.VARCHAR, max_length=64),
                FieldSchema(name="document_version_id", dtype=DataType.VARCHAR, max_length=64),
                FieldSchema(name="parent_id", dtype=DataType.VARCHAR, max_length=64),
                FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=65535),
                FieldSchema(name="source", dtype=DataType.VARCHAR, max_length=512),
                FieldSchema(name="metadata", dtype=DataType.VARCHAR, max_length=65535),
                FieldSchema(name="dense_vector", dtype=DataType.FLOAT_VECTOR, dim=self.dim),
                FieldSchema(name="sparse_vector", dtype=DataType.SPARSE_FLOAT_VECTOR),
            ]
            schema = CollectionSchema(fields, description="agent kb chunks hybrid")
            col = Collection(self.collection_name, schema)
            col.create_index(
                field_name="dense_vector",
                index_params={
                    "index_type": "HNSW",
                    "metric_type": "COSINE",
                    "params": {"M": 16, "efConstruction": 200},
                },
            )
            col.create_index(
                field_name="sparse_vector",
                index_params={
                    "index_type": "SPARSE_INVERTED_INDEX",
                    "metric_type": "IP",
                    "params": {"drop_ratio_build": 0.2},
                },
            )
            logger.info("created milvus collection=%s dim=%s", self.collection_name, self.dim)
        self._collection = Collection(self.collection_name)
        required = {"tenant_id", "kb_id", "document_version_id", "parent_id"}
        actual = {field.name for field in self._collection.schema.fields}
        if not required.issubset(actual):
            raise RuntimeError(
                f"collection {self.collection_name} has incompatible schema; create a v2 collection"
            )
        self._collection.load()
        self._connected = True

    def _ensure(self) -> None:
        if not self._connected:
            self.connect()

    @staticmethod
    def _to_sparse_dict(sparse: dict[int, float] | None) -> dict[int, float]:
        if not sparse:
            return {0: 0.0}
        # Milvus sparse 要求非空；过滤非正值
        cleaned = {int(k): float(v) for k, v in sparse.items() if float(v) > 0}
        return cleaned or {0: 0.0}

    async def upsert_chunks(self, chunks: list[Chunk]) -> None:
        await asyncio.to_thread(self._ensure)
        assert self._collection is not None
        if not chunks:
            return
        entities = [
            [c.chunk_id for c in chunks],
            [c.doc_id for c in chunks],
            [str(c.metadata.get("tenant_id", ""))[:64] for c in chunks],
            [str(c.metadata.get("kb_id", ""))[:64] for c in chunks],
            [str(c.metadata.get("document_version_id", ""))[:64] for c in chunks],
            [c.parent_id or "" for c in chunks],
            [c.text[:65000] for c in chunks],
            [str(c.metadata.get("source", ""))[:500] for c in chunks],
            [json.dumps(c.metadata, ensure_ascii=False)[:65000] for c in chunks],
            [c.dense_vector or [0.0] * self.dim for c in chunks],
            [self._to_sparse_dict(c.sparse_vector) for c in chunks],
        ]
        await asyncio.to_thread(self._collection.upsert, entities)
        await asyncio.to_thread(self._collection.flush)
        logger.info("milvus upsert n=%s", len(chunks))

    async def delete_by_doc_id(self, doc_id: str) -> None:
        await asyncio.to_thread(self._ensure)
        assert self._collection is not None
        expr = f'doc_id == "{doc_id}"'
        await asyncio.to_thread(self._collection.delete, expr)
        await asyncio.to_thread(self._collection.flush)

    def _hits_from_results(self, results: Any) -> list[RetrievalHit]:
        hits: list[RetrievalHit] = []
        for hit in results[0]:
            entity = hit.entity
            meta_raw = entity.get("metadata") or "{}"
            try:
                metadata = json.loads(meta_raw)
            except json.JSONDecodeError:
                metadata = {}
            hits.append(
                RetrievalHit(
                    chunk_id=entity.get("chunk_id"),
                    doc_id=entity.get("doc_id"),
                    text=entity.get("text") or "",
                    source=entity.get("source") or "",
                    score=float(hit.distance),
                    metadata=metadata,
                    parent_id=entity.get("parent_id") or None,
                    tenant_id=entity.get("tenant_id") or None,
                    kb_id=entity.get("kb_id") or None,
                    document_version_id=entity.get("document_version_id") or None,
                )
            )
        return hits

    async def search(
        self, vector: list[float], top_k: int, *, tenant_id: str | None = None, kb_ids: set[str] | None = None
    ) -> list[RetrievalHit]:
        await asyncio.to_thread(self._ensure)
        assert self._collection is not None
        results = await asyncio.to_thread(
            self._collection.search,
            data=[vector],
            anns_field="dense_vector",
            param={"metric_type": "COSINE", "params": {"ef": 64}},
            limit=top_k,
            expr=self._scope_expr(tenant_id, kb_ids),
            output_fields=["chunk_id", "doc_id", "text", "source", "metadata", "parent_id", "tenant_id", "kb_id", "document_version_id"],
        )
        return self._hits_from_results(results)

    async def sparse_search(
        self, sparse: dict[int, float], top_k: int, *, tenant_id: str | None = None, kb_ids: set[str] | None = None
    ) -> list[RetrievalHit]:
        await asyncio.to_thread(self._ensure)
        assert self._collection is not None
        query = self._to_sparse_dict(sparse)
        results = await asyncio.to_thread(
            self._collection.search,
            data=[query],
            anns_field="sparse_vector",
            param={"metric_type": "IP", "params": {"drop_ratio_search": 0.2}},
            limit=top_k,
            expr=self._scope_expr(tenant_id, kb_ids),
            output_fields=["chunk_id", "doc_id", "text", "source", "metadata", "parent_id", "tenant_id", "kb_id", "document_version_id"],
        )
        return self._hits_from_results(results)

    @staticmethod
    def _scope_expr(tenant_id: str | None, kb_ids: set[str] | None) -> str | None:
        if not tenant_id or not kb_ids:
            return None
        safe_tenant = tenant_id.replace('"', "")
        safe_kbs = ", ".join(f'"{kb.replace(chr(34), "")}"' for kb in sorted(kb_ids))
        return f'tenant_id == "{safe_tenant}" && kb_id in [{safe_kbs}]'


class InMemoryVectorStore:
    """单测 / 极端离线兜底；生产路径不得默认使用。"""

    def __init__(self) -> None:
        self._chunks: dict[str, Chunk] = {}

    async def upsert_chunks(self, chunks: list[Chunk]) -> None:
        for c in chunks:
            self._chunks[c.chunk_id] = c

    async def delete_by_doc_id(self, doc_id: str) -> None:
        self._chunks = {k: v for k, v in self._chunks.items() if v.doc_id != doc_id}

    async def search(self, vector: list[float], top_k: int, **_: Any) -> list[RetrievalHit]:
        from rag.retrieve.service import cosine

        scored: list[tuple[float, Chunk]] = []
        for c in self._chunks.values():
            if not c.dense_vector:
                continue
            scored.append((cosine(vector, c.dense_vector), c))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            RetrievalHit(
                chunk_id=c.chunk_id,
                doc_id=c.doc_id,
                text=c.text,
                source=str(c.metadata.get("source", "")),
                score=score,
                metadata=c.metadata,
                parent_id=c.parent_id,
                tenant_id=str(c.metadata.get("tenant_id") or "") or None,
                kb_id=str(c.metadata.get("kb_id") or "") or None,
                document_version_id=str(c.metadata.get("document_version_id") or "") or None,
            )
            for score, c in scored[:top_k]
        ]

    async def sparse_search(self, sparse: dict[int, float], top_k: int, **_: Any) -> list[RetrievalHit]:
        scored: list[tuple[float, Chunk]] = []
        for c in self._chunks.values():
            if not c.sparse_vector:
                continue
            score = sum(sparse.get(i, 0.0) * v for i, v in c.sparse_vector.items())
            scored.append((score, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            RetrievalHit(
                chunk_id=c.chunk_id,
                doc_id=c.doc_id,
                text=c.text,
                source=str(c.metadata.get("source", "")),
                score=score,
                metadata=c.metadata,
                parent_id=c.parent_id,
                tenant_id=str(c.metadata.get("tenant_id") or "") or None,
                kb_id=str(c.metadata.get("kb_id") or "") or None,
                document_version_id=str(c.metadata.get("document_version_id") or "") or None,
            )
            for score, c in scored[:top_k]
        ]
