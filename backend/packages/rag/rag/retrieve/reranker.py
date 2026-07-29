"""HTTP reranker adapter for a separately deployed BGE-compatible service."""

from __future__ import annotations

from typing import Any

import httpx

from rag.models import RetrievalHit


class HttpReranker:
    def __init__(self, url: str) -> None:
        self.url = url.rstrip("/")

    async def rerank(self, query: str, hits: list[RetrievalHit], top_k: int) -> list[RetrievalHit]:
        payload: dict[str, Any] = {"query": query, "documents": [hit.text for hit in hits], "top_n": top_k}
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(f"{self.url}/rerank", json=payload)
            response.raise_for_status()
        rows = response.json().get("results") or []
        ranked: list[RetrievalHit] = []
        for row in rows:
            index = int(row.get("index", -1))
            if 0 <= index < len(hits):
                ranked.append(hits[index].model_copy(update={"score": float(row.get("relevance_score", 0.0))}))
        return ranked
