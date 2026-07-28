"""AgentRuntime：策略路由 + Postgres checkpoint + 真流式 SSE 投影。"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

from shared.config import settings
from shared.logging import get_logger
from shared.schemas import AgentStrategy, ChatEvent

from agent_core.nodes import llm_route_strategy
from agent_core.state import AgentState
from agent_core.strategies import build_cot_graph, build_plan_execute_graph, build_react_graph
from agent_core.tools.registry import ToolRegistry

logger = get_logger(__name__)


class AgentRuntime:
    def __init__(self, *, llm: Any, tools: ToolRegistry) -> None:
        self.llm = llm
        self.tools = tools
        self._checkpointer: Any = None
        self._pg_pool: Any = None
        self._graphs: dict[AgentStrategy, Any] = {}

    async def setup_checkpointer(self) -> None:
        """挂载 Postgres checkpointer；失败时降级为无持久化图。"""
        if not settings.agent_enable_checkpoint:
            self._rebuild_graphs(checkpointer=None)
            return
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            from psycopg_pool import AsyncConnectionPool

            self._pg_pool = AsyncConnectionPool(
                conninfo=settings.postgres_dsn_sync,
                min_size=1,
                max_size=5,
                open=False,
            )
            await self._pg_pool.open()
            saver = AsyncPostgresSaver(self._pg_pool)
            await saver.setup()
            self._checkpointer = saver
            self._rebuild_graphs(checkpointer=saver)
            logger.info("langgraph postgres checkpointer ready")
        except Exception as exc:  # noqa: BLE001
            logger.warning("checkpointer unavailable, running without persistence: %s", exc)
            self._checkpointer = None
            self._pg_pool = None
            self._rebuild_graphs(checkpointer=None)

    def _rebuild_graphs(self, *, checkpointer: Any) -> None:
        self._graphs = {
            AgentStrategy.COT: build_cot_graph(
                llm=self.llm, tools=self.tools, checkpointer=checkpointer
            ),
            AgentStrategy.REACT: build_react_graph(
                llm=self.llm, tools=self.tools, checkpointer=checkpointer
            ),
            AgentStrategy.PLAN_EXECUTE: build_plan_execute_graph(
                llm=self.llm, tools=self.tools, checkpointer=checkpointer
            ),
        }

    async def aclose(self) -> None:
        await self.tools.aclose()
        if self._pg_pool is not None:
            await self._pg_pool.close()
            self._pg_pool = None
        self._checkpointer = None

    async def resolve_strategy(self, strategy: AgentStrategy, message: str) -> AgentStrategy:
        if strategy != AgentStrategy.AUTO:
            return strategy
        return await llm_route_strategy(self.llm, message)

    async def run_stream(
        self,
        *,
        message: str,
        strategy: AgentStrategy = AgentStrategy.AUTO,
        enable_rag: bool = True,
        session_id: str | None = None,
        resume_value: Any | None = None,
    ) -> AsyncIterator[ChatEvent]:
        if not self._graphs:
            self._rebuild_graphs(checkpointer=None)

        chosen = await self.resolve_strategy(strategy, message)
        thread_id = session_id or str(uuid.uuid4())
        yield ChatEvent(
            type="strategy",
            data={"strategy": chosen.value, "session_id": thread_id},
        )

        graph = self._graphs[chosen]
        config = {"configurable": {"thread_id": thread_id}}
        initial: AgentState = {
            "message": message,
            "strategy": chosen.value,
            "enable_rag": enable_rag,
            "thoughts": [],
            "plan_steps": [],
            "current_step": 0,
            "iterations": 0,
            "pending_tool": None,
            "tool_history": [],
            "context": "",
            "citations": [],
            "final_answer": "",
            "need_more": False,
            "done": False,
            "error": None,
            "awaiting_hitl": False,
        }

        final_answer = ""
        final_citations: list[Any] = []
        final_plan: list[str] = []
        streamed_answer = False

        try:
            if resume_value is not None:
                from langgraph.types import Command

                stream_input: Any = Command(resume=resume_value)
            else:
                stream_input = initial

            async for update in graph.astream(stream_input, config=config, stream_mode="updates"):
                # interrupt 时 update 可能是特殊结构
                if isinstance(update, dict) and "__interrupt__" in update:
                    payload = update["__interrupt__"]
                    yield ChatEvent(
                        type="hitl",
                        data={
                            "session_id": thread_id,
                            "payload": _serialize_interrupt(payload),
                        },
                    )
                    return

                for node_name, delta in update.items():
                    if not isinstance(delta, dict):
                        continue
                    for thought in delta.get("thoughts") or []:
                        yield ChatEvent(type="thought", data={"node": node_name, "text": thought})
                    if delta.get("plan_steps"):
                        final_plan = list(delta["plan_steps"])
                        yield ChatEvent(type="plan", data={"steps": final_plan})
                    if delta.get("awaiting_hitl"):
                        yield ChatEvent(
                            type="hitl",
                            data={
                                "session_id": thread_id,
                                "kind": "plan_approval",
                                "plan_steps": delta.get("plan_steps") or final_plan,
                            },
                        )
                    for item in delta.get("tool_history") or []:
                        if item.get("result") is None:
                            yield ChatEvent(
                                type="tool_start",
                                data={"name": item.get("name"), "arguments": item.get("arguments")},
                            )
                        else:
                            yield ChatEvent(
                                type="tool_end",
                                data={
                                    "name": item.get("name"),
                                    "ok": bool((item.get("result") or {}).get("ok", True)),
                                },
                            )
                    if delta.get("citations"):
                        final_citations = list(delta["citations"])
                        yield ChatEvent(type="citation", data={"citations": final_citations})
                    if delta.get("stream_tokens"):
                        streamed_answer = True
                        for tok in delta["stream_tokens"]:
                            yield ChatEvent(type="token", data={"text": tok})
                    if delta.get("final_answer"):
                        final_answer = delta["final_answer"]
                    if delta.get("error"):
                        yield ChatEvent(type="error", data={"message": delta["error"]})
        except Exception as exc:  # noqa: BLE001
            # LangGraph interrupt 可能以异常形式冒泡（版本差异）
            if "Interrupt" in type(exc).__name__ or "interrupt" in str(exc).lower():
                yield ChatEvent(
                    type="hitl",
                    data={"session_id": thread_id, "payload": str(exc)},
                )
                return
            logger.exception("agent run failed")
            yield ChatEvent(type="error", data={"message": str(exc)})
            return

        if final_answer and not streamed_answer:
            # 对未走流式节点的答案做真 LLM 二次流式可选；默认分块推送以保证前端兼容
            async for tok in self._stream_answer_tokens(final_answer):
                yield ChatEvent(type="token", data={"text": tok})

        yield ChatEvent(
            type="final",
            data={
                "answer": final_answer,
                "citations": final_citations,
                "strategy": chosen.value,
                "plan_steps": final_plan,
                "session_id": thread_id,
            },
        )

    async def _stream_answer_tokens(self, answer: str) -> AsyncIterator[str]:
        """优先用 chat_stream 重放式推送过长答案；短答案直接切块。"""
        if len(answer) < 40:
            yield answer
            return
        # 已有完整答案时不再二次调用 LLM，按句/块推送避免双倍计费
        buf = ""
        for ch in answer:
            buf += ch
            if ch in ("。", "！", "？", "\n", ".", "!", "?") and len(buf) >= 12:
                yield buf
                buf = ""
        if buf:
            yield buf


def _serialize_interrupt(payload: Any) -> Any:
    try:
        if isinstance(payload, (list, tuple)) and payload:
            first = payload[0]
            return getattr(first, "value", first)
        return getattr(payload, "value", payload)
    except Exception:  # noqa: BLE001
        return str(payload)
