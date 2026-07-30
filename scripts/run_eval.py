#!/usr/bin/env python3
"""Eval CLI：mock（CI）或 live（需真实 retrieve/agent）。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from eval.runners.agent_eval import run_agent_trajectory_eval
from eval.runners.generation_eval import run_generation_eval
from eval.runners.rag_eval import run_retrieval_eval
from eval.thresholds import check_thresholds, parse_fail_under

ROOT = Path(__file__).resolve().parents[1]
DS_DIR = ROOT / "backend/packages/eval/eval/datasets"


class _Hit:
    def __init__(self, doc_id: str) -> None:
        self.doc_id = doc_id


class _RetrieveResult:
    def __init__(self, hits: list[_Hit], context_text: str = "") -> None:
        self.hits = hits
        self.context_text = context_text


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _to_dict(result: Any) -> dict[str, Any]:
    if is_dataclass(result):
        return asdict(result)
    if isinstance(result, dict):
        return result
    return {"value": result}


async def _mock_retrieve_factory(dataset_path: Path):
    by_q = {row["question"]: row for row in _load_jsonl(dataset_path)}

    async def retrieve(q: str) -> _RetrieveResult:
        row = by_q.get(q) or {}
        ids = list(row.get("golden_doc_ids") or [])
        hits = [_Hit(doc_id) for doc_id in ids]
        ctx = " ".join(ids) + f" context for {q}"
        return _RetrieveResult(hits=hits, context_text=ctx)

    return retrieve


async def _run_retrieval(mode: str, dataset: Path) -> dict[str, Any]:
    if mode == "mock":
        retrieve = await _mock_retrieve_factory(dataset)
        result = await run_retrieval_eval(dataset, retrieve)
        return {"kind": "retrieval", **_to_dict(result)}

    from shared.config import settings  # noqa: F401 — live path needs env

    raise SystemExit("live retrieval requires API path (POST /eval/runs); use --mode mock for CLI")


async def _run_trajectory(mode: str, dataset: Path) -> dict[str, Any]:
    if mode == "mock":

        async def run_fn(row: dict[str, Any]) -> dict[str, Any]:
            tools = list(row.get("expected_tools") or [])
            skills = list(row.get("expected_skills") or [])
            needle = row.get("success_contains") or "ok"
            return {
                "answer": f"mock answer mentions {needle}",
                "tools_used": tools,
                "skills_used": skills,
                "steps": max(len(tools), 1),
            }

        result = await run_agent_trajectory_eval(dataset, run_fn)
        return {"kind": "trajectory", **_to_dict(result)}

    raise SystemExit("live trajectory requires API path (POST /eval/runs); use --mode mock for CLI")


async def _run_generation(mode: str, dataset: Path) -> dict[str, Any]:
    if mode == "mock":
        retrieve = await _mock_retrieve_factory(dataset)

        async def run_fn(row: dict[str, Any], context: str) -> str:
            # 答案仅复述上下文 + 问题，保证启发式指标可复现为满分
            return f"{context} {row['question']}".strip()

        async def judge_fn(*, question: str, answer: str, context: str) -> tuple[float, float]:
            _ = (question, answer, context)
            return 1.0, 1.0

        result = await run_generation_eval(dataset, retrieve, run_fn, judge_fn)
        return {"kind": "generation", **_to_dict(result)}

    raise SystemExit("live generation requires API path (POST /eval/runs); use --mode mock for CLI")


async def _run_kind(kind: str, mode: str) -> dict[str, Any]:
    if kind == "retrieval":
        return await _run_retrieval(mode, DS_DIR / "rag_qa.jsonl")
    if kind == "trajectory":
        return await _run_trajectory(mode, DS_DIR / "agent_tasks.jsonl")
    if kind == "generation":
        return await _run_generation(mode, DS_DIR / "rag_qa.jsonl")
    raise SystemExit(f"unknown kind: {kind}")


def _metrics_from_result(result: dict[str, Any]) -> dict[str, Any]:
    skip = {"kind", "details", "n"}
    return {k: v for k, v in result.items() if k not in skip and not isinstance(v, (list, dict))}


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Agent platform eval harness")
    parser.add_argument(
        "--kind",
        default="all",
        help="retrieval|trajectory|generation|all (comma-separated ok)",
    )
    parser.add_argument("--mode", choices=["mock", "live"], default="mock")
    parser.add_argument(
        "--fail-under",
        default="",
        help="e.g. hit_at_k=1.0,success_rate=1.0,skill_accuracy=1.0",
    )
    parser.add_argument("--out", default="", help="optional JSON output path")
    args = parser.parse_args(argv)

    kinds = (
        ["retrieval", "trajectory", "generation"]
        if args.kind.strip() == "all"
        else [k.strip() for k in args.kind.split(",") if k.strip()]
    )
    thresholds = parse_fail_under(args.fail_under)
    summary: dict[str, Any] = {"mode": args.mode, "results": [], "failures": []}
    merged_metrics: dict[str, Any] = {}

    for kind in kinds:
        result = await _run_kind(kind, args.mode)
        summary["results"].append(result)
        merged_metrics.update(_metrics_from_result(result))
        print(
            f"[{kind}] "
            + " ".join(f"{k}={v:.3f}" if isinstance(v, float) else f"{k}={v}" for k, v in _metrics_from_result(result).items())
        )

    failures = check_thresholds(merged_metrics, thresholds)
    summary["failures"] = failures
    summary["metrics"] = merged_metrics
    if args.out:
        Path(args.out).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        print(json.dumps({"metrics": merged_metrics, "failures": failures}, ensure_ascii=False))

    if failures:
        print("FAIL thresholds: " + "; ".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
