"""Multi-Agent：Supervisor fan-out → 并行专员 → Synthesizer fan-in → Critic。"""

from __future__ import annotations

import json
from typing import Any, Literal

from langgraph.graph import END, StateGraph
from langgraph.types import Send

from agent_core.nodes import critic_answer
from agent_core.prompts import BuiltinPromptProvider, PromptProvider
from agent_core.state import AgentState
from agent_core.tools.registry import ToolRegistry

SpecialistName = Literal["rag", "web", "calc"]
_SPECIALISTS: tuple[SpecialistName, ...] = ("rag", "web", "calc")


def build_multi_agent_graph(
    *,
    llm: Any,
    tools: ToolRegistry,
    checkpointer: Any = None,
    prompts: PromptProvider | None = None,
):
    provider = prompts or BuiltinPromptProvider()

    def _active(state: AgentState) -> set[str]:
        routing = state.get("routing") or {}
        agents = list(state.get("active_agents") or routing.get("agents") or [])
        return {str(a) for a in agents}

    async def supervisor(state: AgentState) -> dict[str, Any]:
        active = _active(state)
        specialists = [a for a in _SPECIALISTS if a in active]
        if not specialists:
            return {
                "agent_tasks": {},
                "agent_results": {},
                "agent_context_parts": {},
                "thoughts": ["Supervisor: 未指定专员，将直接汇总回答。"],
                "agent_events": [
                    {"agent": "supervisor", "phase": "start"},
                    {"agent": "supervisor", "phase": "end", "ok": True},
                ],
            }

        routing = state.get("routing") or {}
        try:
            body = await llm.chat(
                [
                    {
                        "role": "system",
                        "content": provider.get("multi_agent.supervisor"),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"用户问题: {state['message']}\n"
                            f"启用角色: {specialists}\n"
                            f"路由: {json.dumps(routing, ensure_ascii=False)}"
                        ),
                    },
                ],
                temperature=0.0,
            )
            content = body["choices"][0]["message"]["content"] or ""
            tasks = _parse_tasks(content, specialists)
        except Exception:  # noqa: BLE001
            tasks = {a: state["message"] for a in specialists}

        for a in specialists:
            tasks.setdefault(a, state["message"])

        return {
            "agent_tasks": tasks,
            "thoughts": [
                "Supervisor 并行委派: "
                + ", ".join(f"{k}→{(v or '')[:40]}" for k, v in tasks.items())
            ],
            "agent_events": [
                {"agent": "supervisor", "phase": "start"},
                {"agent": "supervisor", "phase": "end", "ok": True, "tasks": tasks},
            ],
        }

    def fan_out(state: AgentState) -> list[Send]:
        """按路由将任务 fan-out 到多个专员；无专员则直达 synthesizer。"""
        active = _active(state)
        sends: list[Send] = []
        if "rag" in active:
            sends.append(Send("rag_agent", state))
        if "web" in active:
            sends.append(Send("web_agent", state))
        if "calc" in active:
            sends.append(Send("calc_agent", state))
        if not sends:
            return [Send("synthesizer", state)]
        return sends

    async def rag_agent(state: AgentState) -> dict[str, Any]:
        task = (state.get("agent_tasks") or {}).get("rag") or state["message"]
        events = [{"agent": "rag", "phase": "start", "task": task}]
        history: list[dict[str, Any]] = []
        tool_result = await tools.call(
            "retrieve", {"query": task}, state=dict(state)
        )
        history.append(
            {
                "name": "retrieve",
                "arguments": {"query": task},
                "result": tool_result,
                "agent": "rag",
            }
        )
        context = ""
        citations: list[Any] = []
        if tool_result.get("ok") and not tool_result.get("skipped"):
            context = tool_result.get("context") or ""
            citations = list(tool_result.get("hits") or [])

        user = f"委派任务: {task}\n用户原问: {state['message']}\n检索上下文:\n{context[:4000]}"
        body = await llm.chat(
            [
                {"role": "system", "content": provider.get("multi_agent.rag")},
                {"role": "user", "content": user},
            ]
        )
        summary = body["choices"][0]["message"]["content"] or ""
        events.append({"agent": "rag", "phase": "end", "ok": True})
        patch: dict[str, Any] = {
            # 只返回本专员增量，由 merge_dicts 与并行分支合并
            "agent_results": {"rag": summary},
            "tool_history": history,
            "thoughts": [f"[rag] {summary[:200]}"],
            "agent_events": events,
        }
        if context:
            patch["agent_context_parts"] = {"rag": context}
        if citations:
            # 串行策略里 citations 无 reducer；并行时用完整列表写入一次即可
            # 多专员同时写会互相覆盖，故把引用文本并入 context_parts，citations 仅 rag 写入
            patch["citations"] = citations
        return patch

    async def web_agent(state: AgentState) -> dict[str, Any]:
        task = (state.get("agent_tasks") or {}).get("web") or state["message"]
        events = [{"agent": "web", "phase": "start", "task": task}]
        history: list[dict[str, Any]] = []
        call_state = dict(state)
        unlocked = set(call_state.get("unlocked_tools") or [])
        if state.get("enable_web_search"):
            unlocked.add("web_search")
            call_state["unlocked_tools"] = sorted(unlocked)
        search_blob = ""
        if "web_search" in unlocked:
            search_res = await tools.call(
                "web_search", {"query": task}, state=call_state
            )
            history.append(
                {
                    "name": "web_search",
                    "arguments": {"query": task},
                    "result": search_res,
                    "agent": "web",
                }
            )
            if search_res.get("ok"):
                lines = []
                for item in search_res.get("results") or []:
                    lines.append(
                        f"- {item.get('title')}: {item.get('snippet')} ({item.get('url')})"
                    )
                search_blob = "\n".join(lines)
            else:
                search_blob = f"搜索失败: {search_res.get('error')}"
        else:
            search_blob = "web_search 未解锁"

        user = f"委派任务: {task}\n用户原问: {state['message']}\n搜索结果:\n{search_blob[:4000]}"
        body = await llm.chat(
            [
                {"role": "system", "content": provider.get("multi_agent.web")},
                {"role": "user", "content": user},
            ]
        )
        summary = body["choices"][0]["message"]["content"] or ""
        events.append({"agent": "web", "phase": "end", "ok": True})
        return {
            "agent_results": {"web": summary},
            "agent_context_parts": {"web": search_blob[:2000]},
            "tool_history": history,
            "thoughts": [f"[web] {summary[:200]}"],
            "agent_events": events,
        }

    async def calc_agent(state: AgentState) -> dict[str, Any]:
        task = (state.get("agent_tasks") or {}).get("calc") or state["message"]
        events = [{"agent": "calc", "phase": "start", "task": task}]
        history: list[dict[str, Any]] = []
        expr = _extract_expr(task)
        calc_res = await tools.call(
            "calculator", {"expression": expr}, state=dict(state)
        )
        history.append(
            {
                "name": "calculator",
                "arguments": {"expression": expr},
                "result": calc_res,
                "agent": "calc",
            }
        )
        user = (
            f"委派任务: {task}\n用户原问: {state['message']}\n"
            f"表达式: {expr}\n计算结果: {json.dumps(calc_res, ensure_ascii=False)}"
        )
        body = await llm.chat(
            [
                {"role": "system", "content": provider.get("multi_agent.calc")},
                {"role": "user", "content": user},
            ]
        )
        summary = body["choices"][0]["message"]["content"] or ""
        events.append({"agent": "calc", "phase": "end", "ok": True})
        calc_ctx = f"expr={expr}; result={json.dumps(calc_res, ensure_ascii=False)}"
        return {
            "agent_results": {"calc": summary},
            "agent_context_parts": {"calc": calc_ctx},
            "tool_history": history,
            "thoughts": [f"[calc] {summary[:200]}"],
            "agent_events": events,
        }

    async def synthesizer(state: AgentState) -> dict[str, Any]:
        results = state.get("agent_results") or {}
        parts_ctx = state.get("agent_context_parts") or {}
        merged_context = "\n\n".join(
            f"[{k}]\n{v}" for k, v in parts_ctx.items() if v
        )
        if not results:
            body = await llm.chat(
                [
                    {
                        "role": "system",
                        "content": provider.get("multi_agent.synthesizer"),
                    },
                    {"role": "user", "content": state["message"]},
                ]
            )
            answer = body["choices"][0]["message"]["content"] or ""
            return {
                "final_answer": answer,
                "context": merged_context or state.get("context") or "",
                "thoughts": ["Synthesizer 直接作答。"],
                "agent_events": [
                    {"agent": "synth", "phase": "start"},
                    {"agent": "synth", "phase": "end", "ok": True},
                ],
            }

        parts = "\n\n".join(f"### {k}\n{v}" for k, v in results.items())
        body = await llm.chat(
            [
                {
                    "role": "system",
                    "content": provider.get("multi_agent.synthesizer"),
                },
                {
                    "role": "user",
                    "content": (
                        f"用户问题: {state['message']}\n\n"
                        f"子智能体结论:\n{parts}\n\n"
                        f"补充材料:\n{merged_context[:3000]}"
                    ),
                },
            ]
        )
        answer = body["choices"][0]["message"]["content"] or ""
        return {
            "final_answer": answer,
            "context": merged_context or state.get("context") or "",
            "thoughts": [
                f"Synthesizer fan-in：已汇总 {len(results)} 个并行专员结论。"
            ],
            "agent_events": [
                {"agent": "synth", "phase": "start"},
                {"agent": "synth", "phase": "end", "ok": True},
            ],
        }

    async def critic(state: AgentState) -> dict[str, Any]:
        require = bool(state.get("enable_rag") and state.get("context"))
        result = await critic_answer(
            llm,
            question=state["message"],
            answer=state.get("final_answer") or "",
            context=state.get("context") or "",
            require_citation=require,
            prompts=provider,
        )
        if result["pass"]:
            return {"done": True, "thoughts": [f"Critic 通过: {result.get('reason', 'ok')}"]}
        return {
            "final_answer": result["revised"],
            "done": True,
            "thoughts": [f"Critic 修订: {result.get('reason', '')}"],
        }

    graph = StateGraph(AgentState)
    graph.add_node("supervisor", supervisor)
    graph.add_node("rag_agent", rag_agent)
    graph.add_node("web_agent", web_agent)
    graph.add_node("calc_agent", calc_agent)
    graph.add_node("synthesizer", synthesizer)
    graph.add_node("critic", critic)
    graph.set_entry_point("supervisor")
    # fan-out：Supervisor 之后并行派发到多个专员（或直达 synthesizer）
    graph.add_conditional_edges("supervisor", fan_out)
    # fan-in：各专员完成后汇合到 synthesizer（LangGraph 等待并行分支齐）
    graph.add_edge("rag_agent", "synthesizer")
    graph.add_edge("web_agent", "synthesizer")
    graph.add_edge("calc_agent", "synthesizer")
    graph.add_edge("synthesizer", "critic")
    graph.add_edge("critic", END)
    return graph.compile(checkpointer=checkpointer)


def _parse_tasks(content: str, specialists: list[str]) -> dict[str, str]:
    start = content.find("{")
    end = content.rfind("}")
    if start < 0 or end <= start:
        return {a: "" for a in specialists}
    try:
        data = json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return {a: "" for a in specialists}
    raw = data.get("tasks") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {a: "" for a in specialists}
    return {a: str(raw.get(a) or "").strip() for a in specialists}


def _extract_expr(text: str) -> str:
    import re

    m = re.search(r"[\d\.\s\+\-\*/\(\)]+", text)
    if m:
        expr = m.group(0).strip()
        if any(c.isdigit() for c in expr) and any(op in expr for op in "+-*/"):
            return expr
    return "0"
