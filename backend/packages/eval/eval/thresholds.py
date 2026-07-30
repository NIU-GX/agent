"""Eval 阈值解析与门禁。"""

from __future__ import annotations

from typing import Any


def parse_fail_under(spec: str | None) -> dict[str, float]:
    """解析 `hit_at_k=1.0,success_rate=0.8` 形式。"""
    if not spec or not str(spec).strip():
        return {}
    out: dict[str, float] = {}
    for part in str(spec).split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, raw = part.split("=", 1)
        key = key.strip()
        if not key:
            continue
        out[key] = float(raw.strip())
    return out


def check_thresholds(metrics: dict[str, Any], thresholds: dict[str, float]) -> list[str]:
    """返回未达标的说明列表；空列表表示通过。"""
    failures: list[str] = []
    for key, minimum in thresholds.items():
        if key not in metrics:
            continue
        value = metrics[key]
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if numeric + 1e-9 < float(minimum):
            failures.append(f"{key}={numeric:.4f} < {float(minimum):.4f}")
    return failures
