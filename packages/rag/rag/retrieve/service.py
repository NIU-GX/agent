"""同步检索编排：Understand → Rewrite → Hybrid → RRF → Rerank → Context Build。"""

from __future__ import annotations

import asyncio
import math
import re
from typing import Any, Protocol

from shared.config import settings
from shared.logging import get_logger

from rag.models import RetrievalHit, RetrievalResult
from rag.sparse import bm25_sparse, tokenize

logger = get_logger(__name__)


class DenseSearcher(Protocol):
    async def search(self, vector: list[float], top_k: int) -> list[RetrievalHit]: ...


class SparseSearcher(Protocol):
    async def search(self, sparse: dict[int, float], top_k: int) -> list[RetrievalHit]: ...


class QueryEmbedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class ChatCompleter(Protocol):
    async def chat(self, messages: list[dict[str, Any]]) -> dict[str, Any]: ...


class RetrieveOptions:
    def __init__(
        self,
        *,
        top_k_recall: int = 20,
        top_k_rerank: int = 6,
        use_multi_query: bool = True,
        use_hyde: bool = False,
        use_rerank: bool = True,
        rrf_k: int = 60,
        max_context_chars: int = 6000,
        rerank_mode: str | None = None,
    ) -> None:
        self.top_k_recall = top_k_recall
        self.top_k_rerank = top_k_rerank
        self.use_multi_query = use_multi_query
        self.use_hyde = use_hyde
        self.use_rerank = use_rerank
        self.rrf_k = rrf_k
        self.max_context_chars = max_context_chars
        self.rerank_mode = rerank_mode or settings.rag_rerank_mode


class RetrieveService:
    """企业 RAG 在线检索入口。Agent 只依赖本类，不感知 Milvus/MQ。"""

    def __init__(
        self,
        *,
        dense: DenseSearcher,
        sparse: SparseSearcher,
        embedder: QueryEmbedder,
        chat: ChatCompleter | None = None,
    ) -> None:
        self.dense = dense
        self.sparse = sparse
        self.embedder = embedder
        self.chat = chat

    async def retrieve(self, query: str, options: RetrieveOptions | None = None) -> RetrievalResult:
        opts = options or RetrieveOptions()
        rewritten = await self._rewrite(query, opts)
        lists = await asyncio.gather(*[self._hybrid_recall(q, opts) for q in rewritten])
        fused = reciprocal_rank_fusion(lists, k=opts.rrf_k)
        reranked = await self._rerank(query, fused, opts)
        context = build_context(reranked, max_chars=opts.max_context_chars)
        return RetrievalResult(
            query=query,
            hits=reranked,
            rewritten_queries=rewritten,
            context_text=context,
        )

    async def _rewrite(self, query: str, opts: RetrieveOptions) -> list[str]:
        queries = [query]
        if not self.chat:
            return queries
        if opts.use_multi_query:
            prompts = [
                {
                    "role": "system",
                    "content": "你是检索查询改写助手。给出 2 条语义等价的中文检索问法，每行一条，不要序号。",
                },
                {"role": "user", "content": query},
            ]
            try:
                body = await self.chat.chat(prompts)
                content = body["choices"][0]["message"]["content"]
                extras = [line.strip("- ").strip() for line in content.splitlines() if line.strip()]
                queries.extend(extras[:2])
            except Exception as exc:  # noqa: BLE001
                logger.warning("multi-query rewrite failed: %s", exc)
        if opts.use_hyde:
            try:
                body = await self.chat.chat(
                    [
                        {
                            "role": "system",
                            "content": "根据问题写一段可能出现在文档中的假设答案，用于向量检索（HyDE）。",
                        },
                        {"role": "user", "content": query},
                    ]
                )
                hyde = body["choices"][0]["message"]["content"].strip()
                if hyde:
                    queries.append(hyde)
            except Exception as exc:  # noqa: BLE001
                logger.warning("hyde failed: %s", exc)
        seen: set[str] = set()
        unique: list[str] = []
        for q in queries:
            if q not in seen:
                seen.add(q)
                unique.append(q)
        return unique

    async def _hybrid_recall(self, query: str, opts: RetrieveOptions) -> list[RetrievalHit]:
        vec = (await self.embedder.embed([query]))[0]
        sparse = bm25_sparse(query)
        dense_hits, sparse_hits = await asyncio.gather(
            self.dense.search(vec, opts.top_k_recall),
            self.sparse.search(sparse, opts.top_k_recall),
        )
        return reciprocal_rank_fusion([dense_hits, sparse_hits], k=opts.rrf_k)

    async def _rerank(
        self,
        query: str,
        hits: list[RetrievalHit],
        opts: RetrieveOptions,
    ) -> list[RetrievalHit]:
        if not opts.use_rerank or not hits:
            return hits[: opts.top_k_rerank]
        candidates = hits[: max(opts.top_k_recall, opts.top_k_rerank)]
        if opts.rerank_mode == "llm" and self.chat:
            try:
                return await self._llm_rerank(query, candidates, opts.top_k_rerank)
            except Exception as exc:  # noqa: BLE001
                logger.warning("llm rerank failed, fallback lexical: %s", exc)
        return lexical_rerank(query, candidates, top_k=opts.top_k_rerank)

    async def _llm_rerank(
        self,
        query: str,
        hits: list[RetrievalHit],
        top_k: int,
    ) -> list[RetrievalHit]:
        assert self.chat is not None
        lines = []
        for i, hit in enumerate(hits[:12]):
            snippet = hit.text.replace("\n", " ")[:280]
            lines.append(f"{i}: {snippet}")
        body = await self.chat.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "你是检索重排器。根据问题相关性从高到低输出候选编号，"
                        "逗号分隔，只输出编号，不要解释。"
                    ),
                },
                {
                    "role": "user",
                    "content": f"问题: {query}\n候选:\n" + "\n".join(lines),
                },
            ],
            temperature=0.0,
        )
        content = body["choices"][0]["message"]["content"]
        order: list[int] = []
        for part in re.split(r"[,，\s]+", content.strip()):
            if part.isdigit():
                idx = int(part)
                if 0 <= idx < len(hits) and idx not in order:
                    order.append(idx)
        for i in range(len(hits)):
            if i not in order:
                order.append(i)
        ranked = []
        for rank, idx in enumerate(order[:top_k]):
            hit = hits[idx]
            ranked.append(hit.model_copy(update={"score": 1.0 / (rank + 1)}))
        return ranked


def lexical_rerank(query: str, hits: list[RetrievalHit], *, top_k: int) -> list[RetrievalHit]:
    scored: list[RetrievalHit] = []
    q_tokens = set(tokenize(query))
    for hit in hits:
        overlap = len(q_tokens & set(tokenize(hit.text)))
        new_score = 0.4 * hit.score + 0.6 * (overlap / max(len(q_tokens), 1))
        scored.append(hit.model_copy(update={"score": new_score}))
    scored.sort(key=lambda h: h.score, reverse=True)
    return scored[:top_k]


def reciprocal_rank_fusion(
    rank_lists: list[list[RetrievalHit]],
    *,
    k: int = 60,
) -> list[RetrievalHit]:
    scores: dict[str, float] = {}
    keep: dict[str, RetrievalHit] = {}
    for hits in rank_lists:
        for rank, hit in enumerate(hits, start=1):
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (k + rank)
            keep[hit.chunk_id] = hit
    ordered = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [keep[cid].model_copy(update={"score": score}) for cid, score in ordered]


def build_context(hits: list[RetrievalHit], *, max_chars: int) -> str:
    parts: list[str] = []
    used = 0
    for i, hit in enumerate(hits, start=1):
        block = f"[{i}] source={hit.source} doc={hit.doc_id}\n{hit.text}\n"
        if used + len(block) > max_chars:
            break
        parts.append(block)
        used += len(block)
    return "\n".join(parts)


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# 兼容旧测试导入
hash_sparse = bm25_sparse
