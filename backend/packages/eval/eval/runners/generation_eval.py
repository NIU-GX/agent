"""生成质量评测：faithfulness + relevancy。"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class GenerationEvalResult:
    faithfulness: float
    relevancy: float
    n: int
    details: list[dict[str, Any]]


async def run_generation_eval(
    dataset_path: str | Path,
    retrieve_fn: Callable[[str], Awaitable[Any]],
    run_fn: Callable[[dict[str, Any], str], Awaitable[str]],
    judge_fn: Callable[..., Awaitable[tuple[float, float]]],
) -> GenerationEvalResult:
    """dataset JSONL: question (+ optional golden fields).

    retrieve_fn(question) -> object with context_text
    run_fn(row, context) -> answer
    judge_fn(question=, answer=, context=) -> (faithfulness, relevancy)
    """
    path = Path(dataset_path)
    rows = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    faith_sum = 0.0
    rel_sum = 0.0
    details: list[dict[str, Any]] = []

    for row in rows:
        q = row["question"]
        retrieved = await retrieve_fn(q)
        context = getattr(retrieved, "context_text", None)
        if context is None and isinstance(retrieved, dict):
            context = retrieved.get("context_text") or ""
        context = str(context or "")
        answer = await run_fn(row, context)
        faith, rel = await judge_fn(question=q, answer=answer, context=context)
        faith_sum += float(faith)
        rel_sum += float(rel)
        details.append(
            {
                "question": q,
                "faithfulness": float(faith),
                "relevancy": float(rel),
                "answer": (answer or "")[:500],
            }
        )

    n = max(len(rows), 1)
    return GenerationEvalResult(
        faithfulness=faith_sum / n,
        relevancy=rel_sum / n,
        n=len(rows),
        details=details,
    )
