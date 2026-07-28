"""Agent Trajectory 评测：工具选择、步数、是否成功。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable


@dataclass
class TrajectoryEvalResult:
    success_rate: float
    avg_steps: float
    tool_accuracy: float
    n: int
    details: list[dict[str, Any]]


async def run_agent_trajectory_eval(
    dataset_path: str | Path,
    run_fn: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
) -> TrajectoryEvalResult:
    """dataset JSONL: question, strategy, expected_tools(list), success_contains(str)."""
    path = Path(dataset_path)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    success = 0
    steps_sum = 0
    tool_ok = 0
    details: list[dict[str, Any]] = []

    for row in rows:
        out = await run_fn(row)
        answer = out.get("answer") or ""
        tools_used = out.get("tools_used") or []
        steps = int(out.get("steps") or len(tools_used))
        steps_sum += steps
        expected = set(row.get("expected_tools") or [])
        used = set(tools_used)
        t_acc = 1.0 if not expected or expected.issubset(used) else 0.0
        tool_ok += t_acc
        needle = row.get("success_contains") or ""
        ok = (needle in answer) if needle else bool(answer.strip())
        success += int(ok)
        details.append(
            {
                "question": row["question"],
                "ok": ok,
                "tools_used": tools_used,
                "steps": steps,
            }
        )

    n = max(len(rows), 1)
    return TrajectoryEvalResult(
        success_rate=success / n,
        avg_steps=steps_sum / n,
        tool_accuracy=tool_ok / n,
        n=len(rows),
        details=details,
    )
