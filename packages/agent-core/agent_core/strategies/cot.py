"""CoT（Chain-of-Thought）策略图：Think → Answer(stream) → Critic。"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

from agent_core.nodes import critic_answer
from agent_core.state import AgentState
from agent_core.tools.registry import ToolRegistry


def build_cot_graph(*, llm: Any, tools: ToolRegistry, checkpointer: Any = None):
    async def retrieve_once(state: AgentState) -> dict[str, Any]:
        if not state.get("enable_rag"):
            return {}
        result = await tools.call("retrieve", {"query": state["message"]})
        if not result.get("ok"):
            return {"thoughts": [f"检索失败: {result.get('error')}"]}
        return {
            "context": result.get("context", ""),
            "citations": result.get("hits", []),
            "thoughts": ["已完成知识库检索，开始 CoT 推理。"],
        }

    async def think(state: AgentState) -> dict[str, Any]:
        system = (
            "你是严谨的企业助手。请使用逐步推理（Chain-of-Thought），"
            "先写出简短推理步骤，再给出结论。格式：推理:\\n...\\n回答:\\n..."
            "若有上下文，必须基于上下文并注明依据。"
        )
        user = state["message"]
        if state.get("context"):
            user = f"上下文:\n{state['context']}\n\n问题:\n{state['message']}"

        # 真流式：边生成边收集，供 SSE token 事件
        tokens: list[str] = []
        buf = ""
        async for piece in llm.chat_stream(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
        ):
            tokens.append(piece)
            buf += piece
        thought, answer = _split_cot(buf)
        return {
            "thoughts": [thought] if thought else ["完成一步推理"],
            "final_answer": answer or buf,
            "stream_tokens": tokens if "回答:" in buf or "答案:" in buf else tokens,
            "done": False,
        }

    async def critic(state: AgentState) -> dict[str, Any]:
        result = await critic_answer(
            llm,
            question=state["message"],
            answer=state.get("final_answer") or "",
            context=state.get("context") or "",
            require_citation=bool(state.get("enable_rag") and state.get("context")),
        )
        if result["pass"]:
            return {"done": True, "thoughts": [f"Critic 通过: {result.get('reason', 'ok')}"]}
        return {
            "final_answer": result["revised"],
            "need_more": True,
            "done": True,
            "thoughts": [f"Critic 修订: {result.get('reason', '')}"],
        }

    graph = StateGraph(AgentState)
    graph.add_node("retrieve_once", retrieve_once)
    graph.add_node("think", think)
    graph.add_node("critic", critic)
    graph.set_entry_point("retrieve_once")
    graph.add_edge("retrieve_once", "think")
    graph.add_edge("think", "critic")
    graph.add_edge("critic", END)
    return graph.compile(checkpointer=checkpointer)


def _split_cot(content: str) -> tuple[str, str]:
    for ans_key in ("回答:", "答案:", "Answer:", "Final:"):
        if ans_key in content:
            thought, answer = content.split(ans_key, 1)
            return thought.replace("推理:", "").strip(), answer.strip()
    return content.strip(), content.strip()
