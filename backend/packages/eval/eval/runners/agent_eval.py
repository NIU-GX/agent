"""Agent Trajectory 评测：工具/技能选择、步数、是否成功。"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class TrajectoryEvalResult:
    success_rate: float
    avg_steps: float
    tool_accuracy: float
    skill_accuracy: float
    n: int
    details: list[dict[str, Any]]


async def run_agent_trajectory_eval(
    dataset_path: str | Path,
    run_fn: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
) -> TrajectoryEvalResult:
    """dataset JSONL: question, strategy, expected_tools, expected_skills, success_contains.

    run_fn 可额外返回 deepeval_tool_correctness (0~1)；若有则优先用于 tool_accuracy。
    """
    path = Path(dataset_path)
    rows = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    success = 0
    steps_sum = 0
    tool_ok = 0.0
    skill_ok = 0
    skill_scored = 0
    details: list[dict[str, Any]] = []

    for row in rows:
        out = await run_fn(row)
        answer = out.get("answer") or ""
        tools_used = out.get("tools_used") or []
        skills_used = out.get("skills_used") or []
        steps = int(out.get("steps") or len(tools_used))
        steps_sum += steps
        expected_tools = set(row.get("expected_tools") or [])
        used_tools = set(tools_used)
        subset_acc = 1.0 if not expected_tools or expected_tools.issubset(used_tools) else 0.0
        de_tool = out.get("deepeval_tool_correctness")
        t_acc = float(de_tool) if de_tool is not None else subset_acc
        tool_ok += t_acc

        expected_skills = set(row.get("expected_skills") or [])
        used_skills = set(skills_used)
        if expected_skills:
            skill_scored += 1
            s_acc = 1.0 if expected_skills.issubset(used_skills) else 0.0
            skill_ok += s_acc
        else:
            s_acc = 1.0

        needle = row.get("success_contains") or ""
        ok = (needle in answer) if needle else bool(str(answer).strip())
        success += int(ok)
        detail: dict[str, Any] = {
            "question": row["question"],
            "ok": ok,
            "tools_used": tools_used,
            "skills_used": skills_used,
            "steps": steps,
            "tool_ok": bool(t_acc >= 0.999) if de_tool is None else t_acc,
            "skill_ok": bool(s_acc),
        }
        if de_tool is not None:
            detail["deepeval_tool_correctness"] = float(de_tool)
        details.append(detail)

    n = max(len(rows), 1)
    skill_den = max(skill_scored, 1)
    return TrajectoryEvalResult(
        success_rate=success / n,
        avg_steps=steps_sum / n,
        tool_accuracy=tool_ok / n,
        skill_accuracy=(skill_ok / skill_den) if skill_scored else 1.0,
        n=len(rows),
        details=details,
    )
