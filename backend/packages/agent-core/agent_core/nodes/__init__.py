"""公共节点：Critic / Responder / Strategy Router / Intent Router。"""

from __future__ import annotations

import json
from typing import Any

from shared.config import settings
from shared.logging import get_logger
from shared.schemas import AgentStrategy

from agent_core.nodes.intent import (
    RoutingPlan,
    classify_routing,
    heuristic_routing,
)
from agent_core.prompts import BuiltinPromptProvider, PromptProvider

logger = get_logger(__name__)

__all__ = [
    "RoutingPlan",
    "classify_routing",
    "heuristic_routing",
    "llm_route_strategy",
    "heuristic_route",
    "critic_answer",
]


async def llm_route_strategy(
    llm: Any,
    message: str,
    *,
    prompts: PromptProvider | None = None,
) -> AgentStrategy:
    """用 LLM 做策略路由；失败时回退启发式。"""
    provider = prompts or BuiltinPromptProvider()
    try:
        body = await llm.chat(
            [
                {
                    "role": "system",
                    "content": provider.get("router.system"),
                },
                {"role": "user", "content": message},
            ],
            temperature=0.0,
        )
        text = (body["choices"][0]["message"]["content"] or "").strip().lower()
        for cand in ("multi_agent", "plan_execute", "react", "cot"):
            if cand in text:
                return AgentStrategy(cand)
    except Exception as exc:  # noqa: BLE001
        logger.warning("strategy llm route failed: %s", exc)
    return heuristic_route(message)


def heuristic_route(message: str) -> AgentStrategy:
    if any(k in message for k in ("对比", "多步", "计划", "分析一下再", "调研", "分别")):
        if any(k in message for k in ("网上", "最新", "知识库", "政策")):
            return AgentStrategy.MULTI_AGENT
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
    prompts: PromptProvider | None = None,
) -> dict[str, Any]:
    """LLM Critic：检查幻觉与是否需要修订。"""
    if not answer.strip():
        return {"pass": False, "revised": "未能生成有效回答。", "reason": "empty"}
    provider = prompts or BuiltinPromptProvider()
    prompt = [
        {
            "role": "system",
            "content": provider.get("critic.system"),
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
