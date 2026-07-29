#!/usr/bin/env python3
"""本地跑评测 CLI：在无 HTTP 服务时直接调用评测 runner。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from eval.runners.rag_eval import run_retrieval_eval


async def main() -> None:
    ds = (
        Path(__file__).resolve().parents[1]
        / "backend/packages/eval/eval/datasets/rag_qa.jsonl"
    )

    class Dummy:
        hits = []

    async def retrieve(_q: str):
        return Dummy()

    result = await run_retrieval_eval(ds, retrieve)
    print(
        f"retrieval n={result.n} hit@k={result.hit_at_k:.3f} "
        f"recall@k={result.recall_at_k:.3f} mrr={result.mrr:.3f}"
    )


if __name__ == "__main__":
    asyncio.run(main())
