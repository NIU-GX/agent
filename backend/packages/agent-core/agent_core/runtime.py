"""AgentRuntime：能力路由 + 策略图 + Postgres checkpoint + 真流式 SSE 投影。"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

from shared.config import settings
from shared.logging import get_logger
from shared.schemas import AgentStrategy, ChatEvent

from agent_core.nodes import classify_routing, llm_route_strategy
from agent_core.nodes.intent import RoutingPlan
from agent_core.prompts import BuiltinPromptProvider, PromptProvider
from agent_core.skills.registry import SkillRegistry
from agent_core.state import AgentState
from agent_core.strategies import (
    build_cot_graph,
    build_multi_agent_graph,
    build_plan_execute_graph,
    build_react_graph,
)
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
            AgentStrategy.MULTI_AGENT: build_multi_agent_graph(
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

    def _available_skill_names(self) -> list[str]:
        if not self.skills:
            return []
        return [item["name"] for item in self.skills.catalog()]

    async def resolve_routing(
        self,
        *,
        message: str,
        strategy: AgentStrategy,
        enable_rag: bool | None,
        skills: list[str] | None,
    ) -> tuple[AgentStrategy, RoutingPlan, bool]:
        """能力路由：决定 strategy / rag / web / skills / agents。

        返回 (chosen_strategy, plan, effective_enable_rag)。
        enable_rag 显式传入时覆盖路由结果；strategy!=AUTO 时保留用户策略。
        """
        available = self._available_skill_names()
        plan = await classify_routing(
            self.llm,
            message,
            available_skills=available,
            prompts=self.prompts,
        )

        if strategy != AgentStrategy.AUTO:
            chosen = strategy
            plan.strategy = strategy.value
        else:
            try:
                chosen = AgentStrategy(plan.strategy)
            except ValueError:
                chosen = await llm_route_strategy(self.llm, message, prompts=self.prompts)
                plan.strategy = chosen.value

        if enable_rag is None:
            effective_rag = bool(plan.enable_rag)
        else:
            effective_rag = bool(enable_rag)
            plan.enable_rag = effective_rag

        # 合并客户端预选 skills 与路由建议
        client_skills = list(skills or [])
        merged: list[str] = []
        for name in [*plan.skills, *client_skills]:
            if name and name not in merged:
                merged.append(name)
        if plan.enable_web_search and "web-research" not in merged:
            merged.append("web-research")
        if available:
            merged = [s for s in merged if s in set(available)]
        plan.skills = merged

        if chosen == AgentStrategy.MULTI_AGENT and not plan.agents:
            agents: list[str] = []
            if plan.enable_rag:
                agents.append("rag")
            if plan.enable_web_search:
                agents.append("web")
            agents.append("synth")
            plan.agents = agents

        return chosen, plan, effective_rag

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
        enable_rag: bool | None = None,
        session_id: str | None = None,
        resume_value: Any | None = None,
        skills: list[str] | None = None,
        retrieval_scope: dict[str, Any] | None = None,
    ) -> AsyncIterator[ChatEvent]:
        if not self._graphs:
            self._rebuild_graphs(checkpointer=None)

        chosen, plan, effective_rag = await self.resolve_routing(
            message=message,
            strategy=strategy,
            enable_rag=enable_rag,
            skills=skills,
        )
        thread_id = session_id or str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        skill_names = list(plan.skills)
        unlock_extra: set[str] = set()
        if plan.enable_web_search:
            unlock_extra.add("web_search")

        yield ChatEvent(
            type="intent",
            data={
                "enable_rag": plan.enable_rag,
                "enable_web_search": plan.enable_web_search,
                "strategy": plan.strategy,
                "skills": list(plan.skills),
                "agents": list(plan.agents),
                "reason": plan.reason,
            },
        )

        trace = start_trace(
            session_id=thread_id,
            strategy=chosen.value,
            skills=skill_names,
            enable_rag=effective_rag,
            name="agent.run",
        )
        yield ChatEvent(
            type="strategy",
            data={
                "strategy": chosen.value,
                "session_id": thread_id,
                "run_id": run_id,
                "trace_id": trace.trace_id,
                "langfuse_url": trace.langfuse_url,
            },
        )

        pre = self._preactivate_skills(skill_names)
        unlocked = set(pre.get("unlocked_tools") or [])
        unlocked.update(unlock_extra)
        pre["unlocked_tools"] = sorted(unlocked)

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
                "enable_rag": effective_rag,
                "enable_web_search": plan.enable_web_search,
            }
            config["run_name"] = "agent.run"
        initial: AgentState = {
            "message": message,
            "strategy": chosen.value,
            "enable_rag": effective_rag,
            "enable_web_search": bool(plan.enable_web_search),
            "routing": plan.to_dict(),
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
            "active_agents": list(plan.agents),
            "agent_tasks": {},
            "agent_results": {},
            "agent_context_parts": {},
            "agent_events": [],
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
                            "run_id": run_id,
                            "payload": _serialize_interrupt(payload),
                            "trace_id": trace.trace_id,
                            "langfuse_url": trace.langfuse_url,
                        },
                    )
                    return

                for node_name, delta in update.items():
                    if not isinstance(delta, dict):
                        continue
                    for aev in delta.get("agent_events") or []:
                        phase = aev.get("phase")
                        agent_id = aev.get("agent") or node_name
                        if phase == "start":
                            yield ChatEvent(
                                type="agent_start",
                                data={
                                    "agent": agent_id,
                                    "task": aev.get("task"),
                                    "node": node_name,
                                },
                            )
                        elif phase == "end":
                            yield ChatEvent(
                                type="agent_end",
                                data={
                                    "agent": agent_id,
                                    "ok": bool(aev.get("ok", True)),
                                    "node": node_name,
                                },
                            )
                    for thought in delta.get("thoughts") or []:
                        yield ChatEvent(
                            type="thought",
                            data={"node": node_name, "text": thought, "agent": node_name},
                        )
                    if delta.get("plan_steps"):
                        final_plan = list(delta["plan_steps"])
                        yield ChatEvent(type="plan", data={"steps": final_plan})
                    if delta.get("awaiting_hitl"):
                        yield ChatEvent(
                            type="hitl",
                            data={
                                "session_id": thread_id,
                                "run_id": run_id,
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
                                    "agent": item.get("agent"),
                                },
                            )
                        else:
                            yield ChatEvent(
                                type="tool_end",
                                data={
                                    "name": item.get("name"),
                                    "ok": bool((item.get("result") or {}).get("ok", True)),
                                    "agent": item.get("agent"),
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
                        "run_id": run_id,
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
                    "session_id": thread_id,
                    "run_id": run_id,
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
                "run_id": run_id,
                "trace_id": trace.trace_id,
                "langfuse_url": trace.langfuse_url,
                "routing": plan.to_dict(),
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
