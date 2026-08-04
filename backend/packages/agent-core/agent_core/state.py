"""Agent 共享状态与消息结构。"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from shared.schemas import Citation


def merge_lists(left: list[Any], right: list[Any]) -> list[Any]:
    return left + right


def merge_dicts(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    """并行分支写 dict 时按 key 合并（后写覆盖同 key）。"""
    out = dict(left or {})
    out.update(right or {})
    return out


class AgentState(TypedDict, total=False):
    message: str
    strategy: str
    enable_rag: bool
    enable_web_search: bool
    routing: dict[str, Any]
    retrieval_scope: dict[str, Any]
    thoughts: Annotated[list[str], merge_lists]
    plan_steps: list[str]
    current_step: int
    iterations: int
    pending_tool: str | None
    tool_history: Annotated[list[dict[str, Any]], merge_lists]
    context: str
    citations: list[dict[str, Any]]
    final_answer: str
    stream_tokens: list[str]
    need_more: bool
    done: bool
    error: str | None
    awaiting_hitl: bool
    hitl_approved: bool
    # Tool / Skill 渐进披露
    active_skills: list[str]
    unlocked_tools: list[str]
    skill_instructions: list[str]
    skill_events: Annotated[list[dict[str, Any]], merge_lists]
    # Multi-Agent（agent_results / agent_context_parts 支持并行 fan-out 合并）
    active_agents: list[str]
    agent_tasks: dict[str, str]
    agent_results: Annotated[dict[str, str], merge_dicts]
    agent_context_parts: Annotated[dict[str, str], merge_dicts]
    agent_events: Annotated[list[dict[str, Any]], merge_lists]


def citations_from_hits(hits: list[Any]) -> list[Citation]:
    result: list[Citation] = []
    for h in hits:
        result.append(
            Citation(
                chunk_id=getattr(h, "chunk_id", h.get("chunk_id")),
                doc_id=getattr(h, "doc_id", h.get("doc_id")),
                source=getattr(h, "source", h.get("source", "")),
                text=getattr(h, "text", h.get("text", ""))[:500],
                score=float(getattr(h, "score", h.get("score", 0.0))),
            )
        )
    return result
