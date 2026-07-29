"""提示词版本管理：列表、详情、发版、回退。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from shared.schemas import PromptRollbackRequest, PromptVersionCreate

from app.core.security import require_api_key

router = APIRouter(prefix="/prompts", tags=["prompts"])


@router.get("")
async def list_prompts(request: Request, _: None = Depends(require_api_key)):
    store = request.app.state.container.prompts
    return {"items": await store.list_prompts()}


@router.get("/{key}")
async def get_prompt(key: str, request: Request, _: None = Depends(require_api_key)):
    store = request.app.state.container.prompts
    detail = await store.get_prompt(key)
    if not detail:
        raise HTTPException(status_code=404, detail="prompt not found")
    return detail


@router.post("/{key}/versions")
async def create_version(
    key: str,
    body: PromptVersionCreate,
    request: Request,
    _: None = Depends(require_api_key),
):
    store = request.app.state.container.prompts
    try:
        version = await store.create_version(
            key,
            body.content,
            change_note=body.change_note,
            created_by=body.created_by,
            activate=body.activate,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="prompt not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return version


@router.post("/{key}/rollback")
async def rollback_prompt(
    key: str,
    body: PromptRollbackRequest,
    request: Request,
    _: None = Depends(require_api_key),
):
    """回退到历史版本；不删除更新的版本，可再次前进激活。"""
    store = request.app.state.container.prompts
    try:
        return await store.rollback(key, body.version)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
