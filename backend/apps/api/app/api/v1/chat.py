"""对话 SSE：流式执行与 HITL 恢复；业务侧只落 Langfuse 指针。"""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from shared.logging import get_logger
from shared.schemas import AgentRunOut, AgentStrategy, ChatRequest

from shared.rag_store import Principal

from app.core.security import require_principal

logger = get_logger(__name__)
router = APIRouter()

_POINTER_EVENTS = frozenset({"strategy", "final", "error", "hitl"})


class HitlResumeRequest(BaseModel):
    session_id: str
    strategy: AgentStrategy = AgentStrategy.PLAN_EXECUTE
    approved: bool = True
    plan_steps: list[str] | None = None
    message: str = Field(default="", description="原始问题，用于无 checkpoint 降级重跑")
    enable_rag: bool | None = None


def _status_for_event(event_type: str) -> str:
    if event_type == "final":
        return "completed"
    if event_type == "error":
        return "error"
    if event_type == "hitl":
        return "hitl"
    return "started"


async def _persist_run_pointer(
    container: Any,
    *,
    event_type: str,
    data: dict[str, Any],
    tenant_id: str | None,
) -> None:
    """仅写入 session/run/trace 指针，不落工具轨迹。"""
    run_id = data.get("run_id")
    session_id = data.get("session_id")
    if not run_id or not session_id:
        return
    store = getattr(container, "run_store", None)
    if store is None:
        return
    try:
        await store.upsert(
            run_id=str(run_id),
            session_id=str(session_id),
            trace_id=data.get("trace_id"),
            langfuse_url=data.get("langfuse_url"),
            strategy=data.get("strategy"),
            status=_status_for_event(event_type),
            tenant_id=tenant_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent run pointer persist failed: %s", exc)


@router.post("/chat/stream")
async def chat_stream(
    body: ChatRequest,
    request: Request,
    principal: Principal = Depends(require_principal),
):
    container = request.app.state.container
    session_id = body.session_id or str(uuid.uuid4())
    requested = set(body.knowledge_base_ids or [])
    if requested and (not principal.is_admin and not requested.issubset(principal.kb_ids)):
        raise HTTPException(status_code=403, detail="knowledge base access denied")
    if not requested:
        requested = {item["id"] for item in await container.rag_store.list_knowledge_bases(principal)}
    active_versions = await container.rag_store.active_version_ids(principal.tenant_id, requested)
    scope = {
        "tenant_id": principal.tenant_id,
        "kb_ids": sorted(requested),
        "active_version_ids": sorted(active_versions),
    }

    async def event_generator():
        async for event in container.agent.run_stream(
            message=body.message,
            strategy=body.strategy,
            enable_rag=body.enable_rag,
            session_id=session_id,
            skills=list(body.skills or []),
            retrieval_scope=scope,
        ):
            if event.type in _POINTER_EVENTS:
                await _persist_run_pointer(
                    container,
                    event_type=event.type,
                    data=event.data or {},
                    tenant_id=principal.tenant_id,
                )
            payload = event.model_dump()
            yield f"event: {event.type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/chat/resume")
async def chat_resume(
    body: HitlResumeRequest,
    request: Request,
    principal: Principal = Depends(require_principal),
):
    """HITL 审批后恢复 Plan-and-Execute（依赖 checkpoint thread_id）。"""
    container = request.app.state.container
    requested = {item["id"] for item in await container.rag_store.list_knowledge_bases(principal)}
    scope = {
        "tenant_id": principal.tenant_id,
        "kb_ids": sorted(requested),
        "active_version_ids": sorted(
            await container.rag_store.active_version_ids(principal.tenant_id, requested)
        ),
    }
    resume_value: dict[str, Any] = {
        "approved": body.approved,
        "plan_steps": body.plan_steps,
    }

    async def event_generator():
        async for event in container.agent.run_stream(
            message=body.message or "(resume)",
            strategy=body.strategy,
            enable_rag=body.enable_rag,
            session_id=body.session_id,
            resume_value=resume_value,
            retrieval_scope=scope,
        ):
            if event.type in _POINTER_EVENTS:
                await _persist_run_pointer(
                    container,
                    event_type=event.type,
                    data=event.data or {},
                    tenant_id=principal.tenant_id,
                )
            payload = event.model_dump()
            yield f"event: {event.type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/chat/runs", response_model=list[AgentRunOut])
async def list_chat_runs(
    request: Request,
    session_id: str,
    limit: int = 50,
    principal: Principal = Depends(require_principal),
):
    """按 session 查询 run 指针；完整轨迹请跟 langfuse_url。"""
    store = request.app.state.container.run_store
    items = await store.list_by_session(
        session_id,
        limit=min(max(limit, 1), 200),
        tenant_id=None if principal.is_admin else principal.tenant_id,
    )
    return items


@router.get("/chat/runs/{run_id}", response_model=AgentRunOut)
async def get_chat_run(
    run_id: str,
    request: Request,
    principal: Principal = Depends(require_principal),
):
    store = request.app.state.container.run_store
    item = await store.get(run_id)
    if item is None:
        raise HTTPException(status_code=404, detail="run not found")
    if (
        not principal.is_admin
        and item.get("tenant_id")
        and item["tenant_id"] != principal.tenant_id
    ):
        raise HTTPException(status_code=404, detail="run not found")
    return item
