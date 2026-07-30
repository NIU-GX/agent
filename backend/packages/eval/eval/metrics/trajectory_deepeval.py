"""Trajectory 工具正确性：DeepEval ToolCorrectnessMetric（可选）。"""

from __future__ import annotations

from typing import Any

from shared.logging import get_logger

logger = get_logger(__name__)


async def deepeval_tool_correctness(
    *,
    question: str,
    answer: str,
    tools_used: list[str],
    expected_tools: list[str],
    model: Any | None = None,
    gateway: Any | None = None,
) -> float | None:
    """返回 0~1 tool correctness；不可用时返回 None（调用方回退本地规则）。"""
    if not expected_tools:
        return 1.0
    try:
        from deepeval.metrics import ToolCorrectnessMetric
        from deepeval.test_case import LLMTestCase, ToolCall

        from eval.metrics.deepeval_llm import build_deepeval_model

        judge = model or build_deepeval_model(gateway)
        test_case = LLMTestCase(
            input=question,
            actual_output=answer or "",
            tools_called=[ToolCall(name=n) for n in tools_used],
            expected_tools=[ToolCall(name=n) for n in expected_tools],
        )
        metric = ToolCorrectnessMetric(
            threshold=0.5,
            model=judge,
            include_reason=False,
            async_mode=True,
            should_exact_match=True,
        )
        score = float(await metric.a_measure(test_case, _show_indicator=False))
        return max(0.0, min(1.0, score))
    except Exception as exc:  # noqa: BLE001
        logger.debug("deepeval tool correctness unavailable: %s", exc)
        return None
