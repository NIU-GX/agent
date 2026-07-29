"""Agent 侧提示词端口：仅依赖协议，不依赖 PromptStore / DB。

未注入外部 Provider 时使用内置默认正文，Agent 可独立运行。
"""

from __future__ import annotations

from typing import Protocol


class PromptProvider(Protocol):
    """提示词读取端口；实现方可来自内存、文件或外部版本管理服务。"""

    def get(self, key: str, default: str = "") -> str: ...


# Agent 自有默认正文（功能自洽；不依赖 prompt 管理模块）
BUILTIN_PROMPTS: dict[str, str] = {
    "cot.system": (
        "你是严谨的企业助手。请使用逐步推理（Chain-of-Thought），"
        "先写出简短推理步骤，再给出结论。格式：推理:\\n...\\n回答:\\n..."
        "若有上下文，必须基于上下文并注明依据。"
    ),
    "react.system": (
        "你是 ReAct Agent。按需调用工具收集信息，信息足够时直接给出最终中文回答。"
        "不要编造检索结果。有上下文时必须引用。\n"
        "渐进式披露：先看 Skills/Tools 目录；需要时用 activate_skill 解锁 optional/MCP 工具，"
        "再调用具体工具。核心工具 retrieve/calculator 始终可用。"
    ),
    "plan_execute.planner": (
        "你是任务规划器。将用户问题拆成 2-5 个可执行步骤，"
        "每行一个步骤，不要编号之外的废话。步骤应尽量可检索或可计算。"
    ),
    "plan_execute.executor": "你正在执行多步计划中的单步。只完成本步，给出简洁中文结论。",
    "plan_execute.synthesizer": "将各步骤结论汇总为最终回答，保留关键依据，中文输出。",
    "router.system": (
        "你是 Agent 策略路由器。只输出一个词："
        "cot | react | plan_execute。"
        "cot=纯推理解释；react=需要工具/检索；plan_execute=复杂多步。"
    ),
    "critic.system": (
        "你是答案审查员。输出 JSON："
        '{"pass": true/false, "reason": "...", "revised": "必要时给出修订后的完整答案"}。'
        "若上下文不足却给出具体事实，应 pass=false。"
    ),
}

# 供装配层向独立 PromptStore 种子注册（可选）；Agent 运行时不读取此列表
BUILTIN_PROMPT_SEEDS: list[dict[str, str]] = [
    {
        "key": "cot.system",
        "name": "CoT 系统提示",
        "description": "Chain-of-Thought 策略的 system 角色提示。",
        "content": BUILTIN_PROMPTS["cot.system"],
    },
    {
        "key": "react.system",
        "name": "ReAct 系统提示",
        "description": "ReAct 策略 system 提示（不含动态 Skills/Tools 目录，由运行时追加）。",
        "content": BUILTIN_PROMPTS["react.system"],
    },
    {
        "key": "plan_execute.planner",
        "name": "Plan-Execute 规划器",
        "description": "Plan-and-Execute 的 Planner 节点 system 提示。",
        "content": BUILTIN_PROMPTS["plan_execute.planner"],
    },
    {
        "key": "plan_execute.executor",
        "name": "Plan-Execute 执行器",
        "description": "Plan-and-Execute 单步执行节点 system 提示。",
        "content": BUILTIN_PROMPTS["plan_execute.executor"],
    },
    {
        "key": "plan_execute.synthesizer",
        "name": "Plan-Execute 汇总器",
        "description": "Plan-and-Execute 汇总最终回答的 system 提示。",
        "content": BUILTIN_PROMPTS["plan_execute.synthesizer"],
    },
    {
        "key": "router.system",
        "name": "策略路由器",
        "description": "AUTO 策略路由时的 system 提示。",
        "content": BUILTIN_PROMPTS["router.system"],
    },
    {
        "key": "critic.system",
        "name": "Critic 审查员",
        "description": "答案 Critic 节点的 system 提示。",
        "content": BUILTIN_PROMPTS["critic.system"],
    },
]


class BuiltinPromptProvider:
    """内置 Provider：无外部依赖，保证 Agent 功能独立。"""

    def get(self, key: str, default: str = "") -> str:
        return BUILTIN_PROMPTS.get(key, default)
