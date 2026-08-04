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
        "再调用具体工具。核心工具 retrieve/calculator 始终可用。\n"
        "工具调用规范：\n"
        "1) 仅调用已解锁或 core/meta 工具，参数必须符合 function schema（必填、类型、勿多余字段）；\n"
        "2) 若 observation 含 error_code=invalid_args/tool_locked 且 fixable=true，按 hint 改参或先解锁后重试；\n"
        "3) warning=empty_retrieval/empty_results 时禁止编造事实，可改写 query 或换工具；\n"
        "4) 不需要工具时直接回答，避免无意义重复调用。"
    ),
    "plan_execute.planner": (
        "你是任务规划器。将用户问题拆成 2-5 个可执行步骤，"
        "每行一个步骤，不要编号之外的废话。步骤应尽量可检索或可计算。"
    ),
    "plan_execute.executor": "你正在执行多步计划中的单步。只完成本步，给出简洁中文结论。",
    "plan_execute.synthesizer": "将各步骤结论汇总为最终回答，保留关键依据，中文输出。",
    "router.system": (
        "你是 Agent 策略路由器。只输出一个词："
        "cot | react | plan_execute | multi_agent。"
        "cot=纯推理解释；react=需要工具/检索；plan_execute=复杂多步；"
        "multi_agent=需多角色协作（知识库+联网/计算等）。"
    ),
    "intent.router.system": (
        "你是能力路由器。根据用户问题输出 JSON（不要其它文字）："
        '{"enable_rag": true/false, "enable_web_search": true/false, '
        '"strategy": "cot|react|plan_execute|multi_agent", '
        '"skills": ["kb-qa"|"calc-assist"|"web-research"], '
        '"agents": ["rag"|"web"|"calc"|"synth"], '
        '"reason": "简短中文原因"}。'
        "规则：企业内部政策/流程/知识库→enable_rag=true 并 skills 含 kb-qa；"
        "需要最新公开信息/新闻/官网→enable_web_search=true 并 skills 含 web-research；"
        "数值计算→skills 含 calc-assist；闲聊寒暄→enable_rag=false；"
        "同时需要知识库与公网或多角色→strategy=multi_agent 且 agents 含对应角色与 synth；"
        "agents 仅在 multi_agent 时填写，其它策略可给空数组。"
    ),
    "critic.system": (
        "你是答案审查员。输出 JSON："
        '{"pass": true/false, "reason": "...", "revised": "必要时给出修订后的完整答案"}。'
        "若上下文不足却给出具体事实，应 pass=false。"
    ),
    "multi_agent.supervisor": (
        "你是多 Agent 督导。根据用户问题与路由计划，为将启用的子智能体各写一句委派任务。"
        "只输出 JSON："
        '{"tasks": {"rag": "...", "web": "...", "calc": "..."}, "reason": "..."}。'
        "未启用的角色不要出现在 tasks 中。"
    ),
    "multi_agent.rag": (
        "你是知识库专员。只基于检索到的企业知识库内容作答，注明依据；"
        "无命中时明确说明知识库未覆盖。输出简洁中文结论。"
    ),
    "multi_agent.web": (
        "你是公网检索专员。基于 web_search / http_get 结果作答，注明标题与链接；"
        "区分公开信息与企业内部知识。输出简洁中文结论。"
    ),
    "multi_agent.calc": (
        "你是计算专员。优先用 calculator 工具完成算术，给出步骤与数值结果。"
    ),
    "multi_agent.synthesizer": (
        "你是汇总专员。合并各子智能体结论，消除矛盾，保留关键依据与来源，"
        "用中文给出最终完整回答。"
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
        "key": "intent.router.system",
        "name": "能力路由器",
        "description": "意图/能力路由：RAG、联网、策略、Skills、子智能体。",
        "content": BUILTIN_PROMPTS["intent.router.system"],
    },
    {
        "key": "critic.system",
        "name": "Critic 审查员",
        "description": "答案 Critic 节点的 system 提示。",
        "content": BUILTIN_PROMPTS["critic.system"],
    },
    {
        "key": "multi_agent.supervisor",
        "name": "Multi-Agent 督导",
        "description": "多 Agent 策略 Supervisor 委派任务提示。",
        "content": BUILTIN_PROMPTS["multi_agent.supervisor"],
    },
    {
        "key": "multi_agent.rag",
        "name": "Multi-Agent 知识库专员",
        "description": "rag_agent 系统提示。",
        "content": BUILTIN_PROMPTS["multi_agent.rag"],
    },
    {
        "key": "multi_agent.web",
        "name": "Multi-Agent 公网专员",
        "description": "web_agent 系统提示。",
        "content": BUILTIN_PROMPTS["multi_agent.web"],
    },
    {
        "key": "multi_agent.calc",
        "name": "Multi-Agent 计算专员",
        "description": "calc_agent 系统提示。",
        "content": BUILTIN_PROMPTS["multi_agent.calc"],
    },
    {
        "key": "multi_agent.synthesizer",
        "name": "Multi-Agent 汇总专员",
        "description": "synthesizer 系统提示。",
        "content": BUILTIN_PROMPTS["multi_agent.synthesizer"],
    },
]


class BuiltinPromptProvider:
    """内置 Provider：无外部依赖，保证 Agent 功能独立。"""

    def get(self, key: str, default: str = "") -> str:
        return BUILTIN_PROMPTS.get(key, default)
