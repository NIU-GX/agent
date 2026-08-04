"""能力路由（Intent Router）：产出 RAG / 联网 / 策略 / Skills / 子智能体计划。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from shared.config import settings
from shared.logging import get_logger
from shared.schemas import AgentStrategy

from agent_core.prompts import BuiltinPromptProvider, PromptProvider

logger = get_logger(__name__)

VALID_STRATEGIES = {
    AgentStrategy.COT.value,
    AgentStrategy.REACT.value,
    AgentStrategy.PLAN_EXECUTE.value,
    AgentStrategy.MULTI_AGENT.value,
}
VALID_AGENTS = {"rag", "web", "calc", "synth"}


@dataclass
class RoutingPlan:
    enable_rag: bool = True
    enable_web_search: bool = False
    strategy: str = "react"
    skills: list[str] = field(default_factory=list)
    agents: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def heuristic_routing(
    message: str,
    *,
    available_skills: list[str] | None = None,
) -> RoutingPlan:
    """关键词启发式兜底路由。"""
    available = set(available_skills or [])
    text = message or ""
    chitchat = any(
        k in text for k in ("你好", "您好", "谢谢", "再见", "哈哈", "你是谁", "在吗")
    )
    need_rag = any(k in text for k in ("政策", "制度", "知识库", "流程", "规范", "规定", "SOP"))
    need_web = any(k in text for k in ("最新", "新闻", "网上", "搜索一下", "官网", "公开", "互联网"))
    need_calc = any(k in text for k in ("计算", "合计", "算一下", "加减乘除", "等于多少"))
    multi = any(k in text for k in ("对比", "多步", "调研", "分别", "综合"))

    # 企业助手默认开 RAG；明确闲聊或纯计算则关闭
    if chitchat and not need_rag and not need_web:
        need_rag = False
    elif need_calc and not need_rag and not need_web:
        need_rag = False
    elif not need_rag and not chitchat:
        need_rag = True

    skills: list[str] = []
    if need_rag:
        skills.append("kb-qa")
    if need_web:
        skills.append("web-research")
    if need_calc:
        skills.append("calc-assist")

    agents: list[str] = []
    if need_rag:
        agents.append("rag")
    if need_web:
        agents.append("web")
    if need_calc:
        agents.append("calc")
    if agents:
        agents.append("synth")

    if (need_rag and need_web) or (multi and len([a for a in agents if a != "synth"]) >= 2):
        strategy = AgentStrategy.MULTI_AGENT.value
        if "synth" not in agents:
            agents.append("synth")
        reason = "多源/多角色协作，使用 multi_agent"
    elif multi:
        strategy = AgentStrategy.PLAN_EXECUTE.value
        reason = "复杂多步任务"
    elif any(k in text for k in ("为什么", "解释", "原理", "怎么理解")) and not need_web:
        strategy = AgentStrategy.COT.value
        reason = "解释类问题"
    elif chitchat and not need_rag and not need_web:
        strategy = AgentStrategy.COT.value
        reason = "闲聊，关闭知识库检索"
    else:
        strategy = settings.agent_default_strategy
        if strategy not in VALID_STRATEGIES:
            strategy = AgentStrategy.REACT.value
        reason = "默认策略"

    return RoutingPlan(
        enable_rag=need_rag,
        enable_web_search=need_web,
        strategy=strategy,
        skills=_filter_skills(skills, available),
        agents=_normalize_agents(agents),
        reason=reason,
    )


def _filter_skills(skills: list[str], available: set[str]) -> list[str]:
    if not available:
        return list(dict.fromkeys(skills))
    return [s for s in dict.fromkeys(skills) if s in available]


def _normalize_agents(agents: list[str]) -> list[str]:
    out: list[str] = []
    for a in agents:
        if a in VALID_AGENTS and a not in out:
            out.append(a)
    return out


def _parse_routing_json(
    content: str,
    *,
    available_skills: list[str] | None = None,
) -> RoutingPlan | None:
    start = content.find("{")
    end = content.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    strategy = str(data.get("strategy") or settings.agent_default_strategy).strip().lower()
    if strategy == "auto" or strategy not in VALID_STRATEGIES:
        strategy = settings.agent_default_strategy
        if strategy not in VALID_STRATEGIES:
            strategy = AgentStrategy.REACT.value

    skills_raw = data.get("skills") or []
    if not isinstance(skills_raw, list):
        skills_raw = []
    skills = [str(s).strip() for s in skills_raw if str(s).strip()]

    agents_raw = data.get("agents") or []
    if not isinstance(agents_raw, list):
        agents_raw = []
    agents = _normalize_agents([str(a).strip() for a in agents_raw])

    enable_rag = bool(data.get("enable_rag", True))
    enable_web = bool(data.get("enable_web_search", False))
    if enable_web and "web-research" not in skills:
        skills.append("web-research")
    if enable_rag and "kb-qa" not in skills:
        skills.append("kb-qa")
    if strategy == AgentStrategy.MULTI_AGENT.value and not agents:
        agents = []
        if enable_rag:
            agents.append("rag")
        if enable_web:
            agents.append("web")
        agents.append("synth")

    available = set(available_skills or [])
    return RoutingPlan(
        enable_rag=enable_rag,
        enable_web_search=enable_web,
        strategy=strategy,
        skills=_filter_skills(skills, available),
        agents=agents,
        reason=str(data.get("reason") or "").strip() or "llm routing",
    )


async def classify_routing(
    llm: Any,
    message: str,
    *,
    available_skills: list[str] | None = None,
    prompts: PromptProvider | None = None,
) -> RoutingPlan:
    """LLM 能力路由；失败时回退启发式。"""
    provider = prompts or BuiltinPromptProvider()
    skill_list = ", ".join(available_skills or []) or "（无）"
    try:
        body = await llm.chat(
            [
                {
                    "role": "system",
                    "content": provider.get("intent.router.system"),
                },
                {
                    "role": "user",
                    "content": (
                        f"可用 Skills: {skill_list}\n"
                        f"用户问题:\n{message}"
                    ),
                },
            ],
            temperature=0.0,
        )
        content = body["choices"][0]["message"]["content"] or ""
        plan = _parse_routing_json(content, available_skills=available_skills)
        if plan is not None:
            return plan
        logger.warning("intent router returned unparseable content")
    except Exception as exc:  # noqa: BLE001
        logger.warning("intent llm route failed: %s", exc)
    return heuristic_routing(message, available_skills=available_skills)
