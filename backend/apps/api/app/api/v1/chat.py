"""对话 SSE：流式执行与 HITL 恢复。"""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from shared.schemas import AgentStrategy, ChatRequest

from shared.rag_store import Principal

from app.core.security import require_principal

router = APIRouter()


class HitlResumeRequest(BaseModel):
    session_id: str
    strategy: AgentStrategy = AgentStrategy.PLAN_EXECUTE
    approved: bool = True
    plan_steps: list[str] | None = None
    message: str = Field(default="", description="原始问题，用于无 checkpoint 降级重跑")
    enable_rag: bool = True


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
        from fastapi import HTTPException

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
            payload = event.model_dump()
            yield f"event: {event.type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
