"""Agent 核心：基于 LangGraph 的 CoT / ReAct / Plan-and-Execute。

企业级可控能力：策略切换、max_iterations、Postgres checkpoint、HITL、
Tool / MCP / Skill 渐进式披露。
"""

from __future__ import annotations

from typing import Any

__all__ = ["AgentRuntime", "SkillRegistry", "ToolRegistry", "BuiltinPromptProvider"]


def __getattr__(name: str) -> Any:
    # 延迟导入，避免 import agent_core.prompts 时强拉 LangGraph
    if name == "AgentRuntime":
        from agent_core.runtime import AgentRuntime

        return AgentRuntime
    if name == "SkillRegistry":
        from agent_core.skills import SkillRegistry

        return SkillRegistry
    if name == "ToolRegistry":
        from agent_core.tools import ToolRegistry

        return ToolRegistry
    if name == "BuiltinPromptProvider":
        from agent_core.prompts import BuiltinPromptProvider

        return BuiltinPromptProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
