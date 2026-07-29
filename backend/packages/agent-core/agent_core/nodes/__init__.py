"""公共节点：Critic / Responder / Strategy Router。"""

from __future__ import annotations

import json
from typing import Any

from shared.config import settings
from shared.logging import get_logger
from shared.schemas import AgentStrategy

logger = get_logger(__name__)


async def llm_route_strategy(llm: Any, message: str) -> AgentStrategy:
    """用 LLM 做策略路由；失败时回退启发式。"""
    try:
        body = await llm.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "你是 Agent 策略路由器。只输出一个词："
                        "cot | react | plan_execute。"
                        "cot=纯推理解释；react=需要工具/检索；plan_execute=复杂多步。"
                    ),
                },
                {"role": "user", "content": message},
            ],
            temperature=0.0,
        )
        text = (body["choices"][0]["message"]["content"] or "").strip().lower()
        for cand in ("plan_execute", "react", "cot"):
            if cand in text:
                return AgentStrategy(cand)
    except Exception as exc:  # noqa: BLE001
        logger.warning("strategy llm route failed: %s", exc)
    return heuristic_route(message)


def heuristic_route(message: str) -> AgentStrategy:
    if any(k in message for k in ("对比", "多步", "计划", "分析一下再", "调研", "分别")):
        return AgentStrategy.PLAN_EXECUTE
    if any(k in message for k in ("为什么", "解释", "原理", "怎么理解")):
        return AgentStrategy.COT
    return AgentStrategy(settings.agent_default_strategy)


async def critic_answer(
    llm: Any,
    *,
    question: str,
    answer: str,
    context: str,
    require_citation: bool,
) -> dict[str, Any]:
    """LLM Critic：检查幻觉与是否需要修订。"""
    if not answer.strip():
        return {"pass": False, "revised": "未能生成有效回答。", "reason": "empty"}
    prompt = [
        {
            "role": "system",
            "content": (
                "你是答案审查员。输出 JSON："
                '{"pass": true/false, "reason": "...", "revised": "必要时给出修订后的完整答案"}。'
                "若上下文不足却给出具体事实，应 pass=false。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"问题: {question}\n"
                f"要求引用: {require_citation}\n"
                f"上下文:\n{context[:4000]}\n\n"
                f"答案:\n{answer}"
            ),
        },
    ]
    try:
        body = await llm.chat(prompt, temperature=0.0)
        content = body["choices"][0]["message"]["content"]
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            data = json.loads(content[start : end + 1])
            return {
                "pass": bool(data.get("pass", True)),
                "reason": data.get("reason", ""),
                "revised": data.get("revised") or answer,
            }
    except Exception as exc:  # noqa: BLE001
        logger.warning("critic failed: %s", exc)
    if require_citation and context and not any(x in answer for x in ("根据", "依据", "[", "来源")):
        return {
            "pass": False,
            "reason": "missing citation cue",
            "revised": answer + "\n\n（请结合检索依据核对结论。）",
        }
    return {"pass": True, "reason": "ok", "revised": answer}
