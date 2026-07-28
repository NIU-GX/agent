"""Agent 共享状态与消息结构。"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from shared.schemas import Citation


def merge_lists(left: list[Any], right: list[Any]) -> list[Any]:
    return left + right


class AgentState(TypedDict, total=False):
    message: str
    strategy: str
    enable_rag: bool
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
