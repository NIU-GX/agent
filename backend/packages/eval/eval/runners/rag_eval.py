"""检索评测：Hit@k / Recall@k / MRR。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Awaitable


@dataclass
class RetrievalEvalResult:
    hit_at_k: float
    recall_at_k: float
    mrr: float
    n: int
    details: list[dict[str, Any]]


async def run_retrieval_eval(
    dataset_path: str | Path,
    retrieve_fn: Callable[[str], Awaitable[Any]],
    *,
    k: int = 5,
) -> RetrievalEvalResult:
    """dataset JSONL 字段：question, golden_doc_ids (list[str])."""
    path = Path(dataset_path)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    hits = 0
    recall_sum = 0.0
    mrr_sum = 0.0
    details: list[dict[str, Any]] = []

    for row in rows:
        result = await retrieve_fn(row["question"])
        got_ids = [h.doc_id if hasattr(h, "doc_id") else h.get("doc_id") for h in result.hits[:k]]
        golden = set(row.get("golden_doc_ids") or [])
        hit = 1 if golden & set(got_ids) else 0
        hits += hit
        inter = len(golden & set(got_ids))
        recall_sum += inter / max(len(golden), 1)
        rr = 0.0
        for idx, doc_id in enumerate(got_ids, start=1):
            if doc_id in golden:
                rr = 1.0 / idx
                break
        mrr_sum += rr
        details.append(
            {
                "question": row["question"],
                "hit": hit,
                "got_ids": got_ids,
                "golden_doc_ids": list(golden),
            }
        )

    n = max(len(rows), 1)
    return RetrievalEvalResult(
        hit_at_k=hits / n,
        recall_at_k=recall_sum / n,
        mrr=mrr_sum / n,
        n=len(rows),
        details=details,
    )
