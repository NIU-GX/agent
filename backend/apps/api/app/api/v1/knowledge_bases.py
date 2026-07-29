"""知识库管理：所有读写都绑定 API Key principal。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from shared.rag_store import Principal
from shared.schemas import KnowledgeBaseCreate

from app.core.security import require_principal

router = APIRouter()


@router.get("/knowledge-bases")
async def list_knowledge_bases(
    request: Request, principal: Principal = Depends(require_principal)
):
    return {"items": await request.app.state.container.rag_store.list_knowledge_bases(principal)}


@router.post("/knowledge-bases", status_code=201)
async def create_knowledge_base(
    body: KnowledgeBaseCreate,
    request: Request,
    principal: Principal = Depends(require_principal),
):
    try:
        return await request.app.state.container.rag_store.create_knowledge_base(principal, body.name)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.put("/knowledge-bases/{kb_id}")
async def update_knowledge_base(
    kb_id: str,
    body: KnowledgeBaseCreate,
    request: Request,
    principal: Principal = Depends(require_principal),
):
    try:
        return await request.app.state.container.rag_store.update_knowledge_base(principal, kb_id, body.name)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.delete("/knowledge-bases/{kb_id}", status_code=204)
async def archive_knowledge_base(
    kb_id: str, request: Request, principal: Principal = Depends(require_principal)
) -> Response:
    try:
        await request.app.state.container.rag_store.archive_knowledge_base(principal, kb_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return Response(status_code=204)
