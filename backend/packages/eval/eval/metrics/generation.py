"""生成质量：DeepEval（Faithfulness / Answer Relevancy）+ 启发式兜底。"""

from __future__ import annotations

import json
import re
from typing import Any

from shared.logging import get_logger

from eval.metrics.deepeval_llm import build_deepeval_model

logger = get_logger(__name__)


def faithfulness_heuristic(answer: str, context: str) -> float:
    if not answer.strip():
        return 0.0
    if not context.strip():
        return 0.5
    a = set(answer.lower().split())
    c = set(context.lower().split())
    if not a:
        return 0.0
    return len(a & c) / len(a)


def answer_relevancy_heuristic(answer: str, question: str) -> float:
    if not answer.strip() or not question.strip():
        return 0.0
    a = set(answer.lower().split())
    q = set(question.lower().split())
    return len(a & q) / max(len(q), 1)


def _split_context(context: str) -> list[str]:
    text = (context or "").strip()
    if not text:
        return []
    parts = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    return parts or [text]


async def deepeval_generation_scores(
    *,
    question: str,
    answer: str,
    context: str,
    gateway: Any | None = None,
    model: Any | None = None,
    threshold: float = 0.5,
) -> tuple[float, float]:
    """用 DeepEval FaithfulnessMetric + AnswerRelevancyMetric 打分。

    失败时回退启发式，保证 harness 不因评判器故障中断。
    """
    try:
        from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric
        from deepeval.test_case import LLMTestCase

        judge = model or build_deepeval_model(gateway)
        test_case = LLMTestCase(
            input=question,
            actual_output=answer or "",
            retrieval_context=_split_context(context),
        )
        faith_metric = FaithfulnessMetric(
            threshold=threshold,
            model=judge,
            include_reason=False,
            async_mode=True,
        )
        rel_metric = AnswerRelevancyMetric(
            threshold=threshold,
            model=judge,
            include_reason=False,
            async_mode=True,
        )
        faith = float(await faith_metric.a_measure(test_case, _show_indicator=False))
        rel = float(await rel_metric.a_measure(test_case, _show_indicator=False))
        return max(0.0, min(1.0, faith)), max(0.0, min(1.0, rel))
    except Exception as exc:  # noqa: BLE001
        logger.warning("deepeval generation scores failed, fallback heuristic: %s", exc)
        return (
            faithfulness_heuristic(answer, context),
            answer_relevancy_heuristic(answer, question),
        )


async def faithfulness_llm_judge(
    llm: Any,
    *,
    question: str,
    answer: str,
    context: str,
) -> float:
    """兼容旧 API：仅返回 faithfulness（内部走 DeepEval）。"""
    faith, _ = await deepeval_generation_scores(
        question=question,
        answer=answer,
        context=context,
        gateway=llm,
    )
    return faith


async def faithfulness_llm_judge_legacy(
    llm: Any,
    *,
    question: str,
    answer: str,
    context: str,
) -> float:
    """旧版手写 LLM-as-judge，保留供对照；默认不再使用。"""
    try:
        body = await llm.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "你是 RAG 忠实度评审。只输出 JSON："
                        '{"score": 0.0-1.0, "reason": "..."}。'
                        "score=答案中可被上下文支持的比例。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"问题: {question}\n上下文:\n{context[:3500]}\n\n答案:\n{answer[:2000]}"
                    ),
                },
            ],
            temperature=0.0,
        )
        content = body["choices"][0]["message"]["content"]
        match = re.search(r"\{.*\}", content, re.S)
        if match:
            data = json.loads(match.group(0))
            score = float(data.get("score", 0.0))
            return max(0.0, min(1.0, score))
    except Exception:  # noqa: BLE001
        pass
    return faithfulness_heuristic(answer, context)
