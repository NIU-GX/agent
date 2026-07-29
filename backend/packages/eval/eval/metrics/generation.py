"""生成质量：LLM-as-judge + 启发式兜底。"""

from __future__ import annotations

import json
import re
from typing import Any


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


async def faithfulness_llm_judge(
    llm: Any,
    *,
    question: str,
    answer: str,
    context: str,
) -> float:
    """LLM-as-judge：0~1 faithfulness。失败时回退启发式。"""
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
