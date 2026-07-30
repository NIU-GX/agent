"""AgentRuntime：策略路由 + Postgres checkpoint + 真流式 SSE 投影。"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

from shared.config import settings
from shared.logging import get_logger
from shared.schemas import AgentStrategy, ChatEvent

from agent_core.nodes import llm_route_strategy
from agent_core.prompts import BuiltinPromptProvider, PromptProvider
from agent_core.skills.registry import SkillRegistry
from agent_core.state import AgentState
from agent_core.strategies import build_cot_graph, build_plan_execute_graph, build_react_graph
from agent_core.tools.registry import ToolRegistry
from agent_core.tracing import refresh_trace_ids, start_trace

logger = get_logger(__name__)


class AgentRuntime:
    def __init__(
        self,
        *,
        llm: Any,
        tools: ToolRegistry,
        skills: SkillRegistry | None = None,
        prompt_provider: PromptProvider | None = None,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.skills = skills or getattr(tools, "skills", None)
        # 默认内置 Provider：无提示词管理模块时 Agent 仍可独立运行
        self.prompts: PromptProvider = prompt_provider or BuiltinPromptProvider()
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
                llm=self.llm,
                tools=self.tools,
                checkpointer=checkpointer,
                prompts=self.prompts,
            ),
            AgentStrategy.REACT: build_react_graph(
                llm=self.llm,
                tools=self.tools,
                checkpointer=checkpointer,
                prompts=self.prompts,
            ),
            AgentStrategy.PLAN_EXECUTE: build_plan_execute_graph(
                llm=self.llm,
                tools=self.tools,
                checkpointer=checkpointer,
                prompts=self.prompts,
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
        return await llm_route_strategy(self.llm, message, prompts=self.prompts)

    def _preactivate_skills(self, skill_names: list[str]) -> dict[str, Any]:
        """会话级预激活 Skills → active_skills / unlocked_tools / skill_instructions。"""
        active: list[str] = []
        unlocked: set[str] = set()
        instructions: list[str] = []
        events: list[dict[str, Any]] = []
        if not self.skills or not skill_names:
            return {
                "active_skills": active,
                "unlocked_tools": sorted(unlocked),
                "skill_instructions": instructions,
                "skill_events": events,
            }
        for name in skill_names:
            result = self.skills.activate(name)
            if not result.get("ok"):
                events.append({"name": name, "phase": "error", "error": result.get("error")})
                continue
            active.append(name)
            unlocked.update(result.get("unlocked_tools") or [])
            body = result.get("body") or ""
            instructions.append(f"## Skill: {name}\n{body}".strip())
            events.append({"name": name, "phase": "activated"})
        return {
            "active_skills": active,
            "unlocked_tools": sorted(unlocked),
            "skill_instructions": instructions,
            "skill_events": events,
        }

    async def run_stream(
        self,
        *,
        message: str,
        strategy: AgentStrategy = AgentStrategy.AUTO,
        enable_rag: bool = True,
        session_id: str | None = None,
        resume_value: Any | None = None,
        skills: list[str] | None = None,
        retrieval_scope: dict[str, Any] | None = None,
    ) -> AsyncIterator[ChatEvent]:
        if not self._graphs:
            self._rebuild_graphs(checkpointer=None)

        chosen = await self.resolve_strategy(strategy, message)
        thread_id = session_id or str(uuid.uuid4())
        skill_names = list(skills or [])
        trace = start_trace(
            session_id=thread_id,
            strategy=chosen.value,
            skills=skill_names,
            enable_rag=enable_rag,
            name="agent.run",
        )
        yield ChatEvent(
            type="strategy",
            data={
                "strategy": chosen.value,
                "session_id": thread_id,
                "trace_id": trace.trace_id,
                "langfuse_url": trace.langfuse_url,
            },
        )

        pre = self._preactivate_skills(skill_names)
        for ev in pre.get("skill_events") or []:
            if ev.get("phase") == "activated":
                yield ChatEvent(type="skill_start", data={"name": ev.get("name")})
                yield ChatEvent(
                    type="skill_end",
                    data={"name": ev.get("name"), "ok": True, "source": "preactivate"},
                )
            elif ev.get("phase") == "error":
                yield ChatEvent(
                    type="skill_end",
                    data={
                        "name": ev.get("name"),
                        "ok": False,
                        "error": ev.get("error"),
                        "source": "preactivate",
                    },
                )

        graph = self._graphs[chosen]
        config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
        cbs = trace.callbacks()
        if cbs:
            config["callbacks"] = cbs
            config["metadata"] = {
                "langfuse_session_id": thread_id,
                "strategy": chosen.value,
                "skills": skill_names,
                "enable_rag": enable_rag,
            }
            config["run_name"] = "agent.run"
        initial: AgentState = {
            "message": message,
            "strategy": chosen.value,
            "enable_rag": enable_rag,
            "retrieval_scope": dict(retrieval_scope or {}),
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
            "active_skills": list(pre.get("active_skills") or []),
            "unlocked_tools": list(pre.get("unlocked_tools") or []),
            "skill_instructions": list(pre.get("skill_instructions") or []),
            "skill_events": [],
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
                if isinstance(update, dict) and "__interrupt__" in update:
                    payload = update["__interrupt__"]
                    trace = refresh_trace_ids(trace)
                    trace.flush()
                    yield ChatEvent(
                        type="hitl",
                        data={
                            "session_id": thread_id,
                            "payload": _serialize_interrupt(payload),
                            "trace_id": trace.trace_id,
                            "langfuse_url": trace.langfuse_url,
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
                                "trace_id": trace.trace_id,
                                "langfuse_url": trace.langfuse_url,
                            },
                        )
                    for sev in delta.get("skill_events") or []:
                        yield ChatEvent(
                            type="skill_start",
                            data={"name": sev.get("name"), "phase": sev.get("phase")},
                        )
                        yield ChatEvent(
                            type="skill_end",
                            data={
                                "name": sev.get("name"),
                                "ok": sev.get("phase") == "activated",
                                "source": "activate_skill",
                            },
                        )
                    for item in delta.get("tool_history") or []:
                        if item.get("result") is None:
                            yield ChatEvent(
                                type="tool_start",
                                data={
                                    "name": item.get("name"),
                                    "arguments": item.get("arguments"),
                                },
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
            if "Interrupt" in type(exc).__name__ or "interrupt" in str(exc).lower():
                trace = refresh_trace_ids(trace)
                trace.flush()
                yield ChatEvent(
                    type="hitl",
                    data={
                        "session_id": thread_id,
                        "payload": str(exc),
                        "trace_id": trace.trace_id,
                        "langfuse_url": trace.langfuse_url,
                    },
                )
                return
            logger.exception("agent run failed")
            trace = refresh_trace_ids(trace)
            trace.flush()
            yield ChatEvent(
                type="error",
                data={
                    "message": str(exc),
                    "trace_id": trace.trace_id,
                    "langfuse_url": trace.langfuse_url,
                },
            )
            return

        if final_answer and not streamed_answer:
            async for tok in self._stream_answer_tokens(final_answer):
                yield ChatEvent(type="token", data={"text": tok})

        trace = refresh_trace_ids(trace)
        trace.flush()
        yield ChatEvent(
            type="final",
            data={
                "answer": final_answer,
                "citations": final_citations,
                "strategy": chosen.value,
                "plan_steps": final_plan,
                "session_id": thread_id,
                "trace_id": trace.trace_id,
                "langfuse_url": trace.langfuse_url,
            },
        )

    async def _stream_answer_tokens(self, answer: str) -> AsyncIterator[str]:
        """优先用 chat_stream 重放式推送过长答案；短答案直接切块。"""
        if len(answer) < 40:
            yield answer
            return
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
