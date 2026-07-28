"""ReAct（Reason + Act）策略图：Reason → Act → Observe 循环 + Critic。"""

from __future__ import annotations

import json
from typing import Any

from langgraph.graph import END, StateGraph

from shared.config import settings

from agent_core.nodes import critic_answer
from agent_core.state import AgentState
from agent_core.tools.registry import ToolRegistry


def build_react_graph(*, llm: Any, tools: ToolRegistry, checkpointer: Any = None):
    max_iters = settings.agent_max_iterations

    async def reason(state: AgentState) -> dict[str, Any]:
        iterations = int(state.get("iterations") or 0)
        if iterations >= max_iters:
            return {
                "final_answer": state.get("final_answer")
                or "已达最大推理步数，请基于已有信息作答或缩小问题范围。",
                "done": True,
                "thoughts": [f"达到 max_iterations={max_iters}，强制结束。"],
            }

        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "你是 ReAct Agent。按需调用工具收集信息，信息足够时直接给出最终中文回答。"
                    "不要编造检索结果。有上下文时必须引用。"
                ),
            },
            {"role": "user", "content": state["message"]},
        ]
        for item in state.get("tool_history") or []:
            messages.append(
                {
                    "role": "assistant",
                    "content": f"Thought: 调用 {item.get('name')}",
                    "tool_calls": [
                        {
                            "id": item.get("call_id", "call"),
                            "type": "function",
                            "function": {
                                "name": item.get("name"),
                                "arguments": json.dumps(
                                    item.get("arguments") or {}, ensure_ascii=False
                                ),
                            },
                        }
                    ],
                }
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": item.get("call_id", "call"),
                    "content": json.dumps(item.get("result") or {}, ensure_ascii=False),
                }
            )

        body = await llm.chat(messages, tools=tools.openai_tools_schema())
        message = body["choices"][0]["message"]
        tool_calls = message.get("tool_calls") or []
        content = message.get("content") or ""

        if tool_calls:
            call = tool_calls[0]
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            history = state.get("tool_history") or []
            if history and history[-1].get("name") == name and history[-1].get("arguments") == args:
                return {
                    "thoughts": ["检测到重复工具调用，停止循环并基于已有结果回答。"],
                    "final_answer": content or _fallback_from_history(history),
                    "done": True,
                }
            return {
                "iterations": iterations + 1,
                "pending_tool": name,
                "thoughts": [content or f"决定调用工具 {name}"],
                "tool_history": [
                    {
                        "name": name,
                        "arguments": args,
                        "call_id": call.get("id", "call"),
                        "result": None,
                    }
                ],
                "done": False,
            }

        return {
            "iterations": iterations + 1,
            "final_answer": content,
            "thoughts": ["推理完成，无需更多工具。"],
            "done": True,
        }

    async def act(state: AgentState) -> dict[str, Any]:
        history = list(state.get("tool_history") or [])
        if not history:
            return {"done": True}
        last = history[-1]
        name = last["name"]
        args = last.get("arguments") or {}
        result = await tools.call(name, args)
        updates: dict[str, Any] = {
            "tool_history": [
                {
                    "name": name,
                    "arguments": args,
                    "call_id": last.get("call_id", "call"),
                    "result": result,
                }
            ],
            "thoughts": [f"Observation from {name}"],
            "pending_tool": None,
        }
        if name == "retrieve" and result.get("ok"):
            updates["context"] = result.get("context", "")
            updates["citations"] = result.get("hits", [])
        return updates

    async def critic(state: AgentState) -> dict[str, Any]:
        result = await critic_answer(
            llm,
            question=state["message"],
            answer=state.get("final_answer") or "",
            context=state.get("context") or "",
            require_citation=bool(state.get("enable_rag")),
        )
        return {
            "final_answer": result["revised"],
            "done": True,
            "thoughts": [f"Critic: {result.get('reason', 'ok')}"],
        }

    def route_after_reason(state: AgentState) -> str:
        if state.get("done"):
            return "critic"
        if state.get("pending_tool"):
            return "act"
        return "critic"

    graph = StateGraph(AgentState)
    graph.add_node("reason", reason)
    graph.add_node("act", act)
    graph.add_node("critic", critic)
    graph.set_entry_point("reason")
    graph.add_conditional_edges(
        "reason", route_after_reason, {"act": "act", "critic": "critic"}
    )
    graph.add_edge("act", "reason")
    graph.add_edge("critic", END)
    return graph.compile(checkpointer=checkpointer)


def _fallback_from_history(history: list[dict[str, Any]]) -> str:
    for item in reversed(history):
        result = item.get("result") or {}
        if result.get("context"):
            return f"基于检索结果：\n{result['context'][:1500]}"
    return "未能得到有效工具结果。"
