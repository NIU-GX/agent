"""Plan-and-Execute：Planner → (可选 HITL) → Executor → Synthesizer → Critic。"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

from shared.config import settings

from agent_core.nodes import critic_answer
from agent_core.prompts import BuiltinPromptProvider, PromptProvider
from agent_core.state import AgentState
from agent_core.tools.registry import ToolRegistry


def build_plan_execute_graph(
    *,
    llm: Any,
    tools: ToolRegistry,
    checkpointer: Any = None,
    prompts: PromptProvider | None = None,
):
    provider = prompts or BuiltinPromptProvider()

    async def planner(state: AgentState) -> dict[str, Any]:
        body = await llm.chat(
            [
                {
                    "role": "system",
                    "content": provider.get("plan_execute.planner"),
                },
                {"role": "user", "content": state["message"]},
            ]
        )
        content = body["choices"][0]["message"]["content"]
        steps = [line.strip(" .-)\t") for line in content.splitlines() if line.strip()]
        steps = steps[:5] or [state["message"]]
        return {
            "plan_steps": steps,
            "current_step": 0,
            "thoughts": [f"计划制定完成，共 {len(steps)} 步。"],
            "awaiting_hitl": bool(settings.agent_enable_hitl),
            "done": False,
        }

    async def hitl_gate(state: AgentState) -> dict[str, Any]:
        """人工确认计划；开启 HITL 时 interrupt，恢复后继续执行。"""
        if not settings.agent_enable_hitl:
            return {"awaiting_hitl": False, "hitl_approved": True}
        if state.get("hitl_approved"):
            return {"awaiting_hitl": False}
        from langgraph.types import interrupt

        decision = interrupt(
            {
                "kind": "plan_approval",
                "plan_steps": state.get("plan_steps") or [],
                "message": "请确认或修改执行计划后继续",
            }
        )
        # resume 值：true / {"approved": true, "plan_steps": [...]}
        approved = True
        steps = state.get("plan_steps") or []
        if isinstance(decision, dict):
            approved = bool(decision.get("approved", True))
            if decision.get("plan_steps"):
                steps = list(decision["plan_steps"])
        elif decision is False:
            approved = False
        if not approved:
            return {
                "final_answer": "用户拒绝了执行计划，任务已取消。",
                "done": True,
                "awaiting_hitl": False,
                "hitl_approved": False,
            }
        return {
            "plan_steps": steps,
            "awaiting_hitl": False,
            "hitl_approved": True,
            "thoughts": ["计划已获人工确认。"],
        }

    async def executor(state: AgentState) -> dict[str, Any]:
        if state.get("done"):
            return {}
        steps = state.get("plan_steps") or []
        idx = int(state.get("current_step") or 0)
        if idx >= len(steps):
            return {"done": True}

        step = steps[idx]
        unlocked = set(state.get("unlocked_tools") or [])
        history: list[dict[str, Any]] = []
        context = state.get("context") or ""

        tool_result = await tools.call("retrieve", {"query": step}, state=dict(state))
        history.append(
            {"name": "retrieve", "arguments": {"query": step}, "result": tool_result}
        )
        if tool_result.get("ok"):
            context = tool_result.get("context", "") or context

        calc_hint = None
        if any(ch in step for ch in "+-*/") and any(c.isdigit() for c in step):
            expr = _extract_expr(step)
            calc_hint = await tools.call(
                "calculator", {"expression": expr}, state=dict(state)
            )
            history.append(
                {"name": "calculator", "arguments": {"expression": expr}, "result": calc_hint}
            )

        # 已解锁 optional 工具可按步骤启发式调用 http_get
        if "http_get" in unlocked and "http" in step.lower():
            import re

            urls = re.findall(r"https://[^\s]+", step)
            for url in urls[:1]:
                http_res = await tools.call("http_get", {"url": url}, state=dict(state))
                history.append(
                    {"name": "http_get", "arguments": {"url": url}, "result": http_res}
                )

        user_content = f"总问题: {state['message']}\n当前步骤: {step}\n上下文:\n{context}"
        if calc_hint and calc_hint.get("ok"):
            user_content += f"\n计算结果: {calc_hint.get('result')}"
        skill_body = "\n\n".join(state.get("skill_instructions") or [])
        system = provider.get("plan_execute.executor")
        if skill_body:
            system += f"\n\n已激活 Skill:\n{skill_body}"

        body = await llm.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ]
        )
        partial = body["choices"][0]["message"]["content"]
        prev = state.get("final_answer") or ""
        merged = f"{prev}\n\n### 步骤{idx + 1}: {step}\n{partial}".strip()
        citations = list(state.get("citations") or [])
        if tool_result.get("ok"):
            citations.extend(tool_result.get("hits") or [])
        return {
            "current_step": idx + 1,
            "final_answer": merged,
            "context": context,
            "citations": citations,
            "thoughts": [f"完成步骤 {idx + 1}/{len(steps)}: {step}"],
            "tool_history": history,
            "done": idx + 1 >= len(steps),
        }

    async def synthesizer(state: AgentState) -> dict[str, Any]:
        tokens: list[str] = []
        buf = ""
        async for piece in llm.chat_stream(
            [
                {
                    "role": "system",
                    "content": provider.get("plan_execute.synthesizer"),
                },
                {
                    "role": "user",
                    "content": f"问题: {state['message']}\n中间结果:\n{state.get('final_answer')}",
                },
            ]
        ):
            tokens.append(piece)
            buf += piece
        return {
            "final_answer": buf,
            "stream_tokens": tokens,
            "thoughts": ["已汇总各步骤为最终回答。"],
            "done": False,
        }

    async def critic(state: AgentState) -> dict[str, Any]:
        result = await critic_answer(
            llm,
            question=state["message"],
            answer=state.get("final_answer") or "",
            context=state.get("context") or "",
            require_citation=True,
            prompts=provider,
        )
        return {
            "final_answer": result["revised"],
            "done": True,
            "thoughts": [f"Critic: {result.get('reason', 'ok')}"],
        }

    def route_exec(state: AgentState) -> str:
        if state.get("done") and state.get("final_answer") and state.get("hitl_approved") is False:
            return "end"
        return "synth" if state.get("done") else "executor"

    graph = StateGraph(AgentState)
    graph.add_node("planner", planner)
    graph.add_node("hitl_gate", hitl_gate)
    graph.add_node("executor", executor)
    graph.add_node("synthesizer", synthesizer)
    graph.add_node("critic", critic)
    graph.set_entry_point("planner")
    graph.add_edge("planner", "hitl_gate")

    def route_after_hitl(state: AgentState) -> str:
        if state.get("done"):
            return "end"
        return "executor"

    graph.add_conditional_edges(
        "hitl_gate",
        route_after_hitl,
        {"executor": "executor", "end": END},
    )
    graph.add_conditional_edges(
        "executor",
        route_exec,
        {"executor": "executor", "synth": "synthesizer", "end": END},
    )
    graph.add_edge("synthesizer", "critic")
    graph.add_edge("critic", END)
    return graph.compile(checkpointer=checkpointer)


def _extract_expr(text: str) -> str:
    import re

    m = re.search(r"[\d\.\s\+\-\*/\(\)]+", text)
    return (m.group(0) if m else text).strip()
