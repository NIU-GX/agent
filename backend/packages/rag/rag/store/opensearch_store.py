"""OpenSearch BM25 适配器；生产通过 Outbox 与向量索引并行写入。"""

from __future__ import annotations

from typing import Any

import httpx

from shared.config import settings

from rag.models import Chunk, RetrievalHit


class OpenSearchLexicalStore:
    def __init__(self, *, base_url: str | None = None, index: str | None = None) -> None:
        self.base_url = (base_url or settings.opensearch_url).rstrip("/")
        self.index = index or settings.opensearch_index
        if not self.base_url:
            raise ValueError("OPENSEARCH_URL is required")

    async def ensure_index(self) -> None:
        body = {
            "settings": {"analysis": {"analyzer": {"kb_standard": {"type": "standard"}}}},
            "mappings": {
                "properties": {
                    "tenant_id": {"type": "keyword"},
                    "kb_id": {"type": "keyword"},
                    "document_version_id": {"type": "keyword"},
                    "parent_id": {"type": "keyword"},
                    "doc_id": {"type": "keyword"},
                    "source": {"type": "keyword"},
                    "text": {"type": "text", "analyzer": "kb_standard"},
                }
            },
        }
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.put(f"{self.base_url}/{self.index}", json=body)
            if response.status_code not in {200, 201, 400}:
                response.raise_for_status()

    async def upsert_chunks(self, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        lines: list[str] = []
        for chunk in chunks:
            meta = chunk.metadata
            lines.append('{"index":{"_index":"%s","_id":"%s"}}' % (self.index, chunk.chunk_id))
            import json

            lines.append(
                json.dumps(
                    {
                        "tenant_id": meta.get("tenant_id", ""),
                        "kb_id": meta.get("kb_id", ""),
                        "document_version_id": meta.get("document_version_id", ""),
                        "parent_id": chunk.parent_id or "",
                        "doc_id": chunk.doc_id,
                        "source": meta.get("source", ""),
                        "text": chunk.text,
                    },
                    ensure_ascii=False,
                )
            )
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self.base_url}/_bulk", content="\n".join(lines) + "\n", headers={"Content-Type": "application/x-ndjson"}
            )
            response.raise_for_status()
            if response.json().get("errors"):
                raise RuntimeError("OpenSearch bulk indexing returned errors")

    async def search(
        self, query: str, top_k: int, *, tenant_id: str, kb_ids: set[str]
    ) -> list[RetrievalHit]:
        if not tenant_id or not kb_ids:
            return []
        body = {
            "size": top_k,
            "query": {
                "bool": {
                    "must": [{"match": {"text": {"query": query}}}],
                    "filter": [{"term": {"tenant_id": tenant_id}}, {"terms": {"kb_id": sorted(kb_ids)}}],
                }
            },
        }
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(f"{self.base_url}/{self.index}/_search", json=body)
            response.raise_for_status()
        return [
            RetrievalHit(
                chunk_id=raw["_id"],
                doc_id=raw["_source"]["doc_id"],
                text=raw["_source"].get("text", ""),
                source=raw["_source"].get("source", ""),
                score=float(raw.get("_score") or 0),
                parent_id=raw["_source"].get("parent_id") or None,
                tenant_id=raw["_source"].get("tenant_id") or None,
                kb_id=raw["_source"].get("kb_id") or None,
                document_version_id=raw["_source"].get("document_version_id") or None,
            )
            for raw in response.json()["hits"]["hits"]
        ]
