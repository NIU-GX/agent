"""Skill 管理：列表、创建、更新、删除、启用。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from shared.schemas import EnabledPatch, SkillCreate, SkillUpdate

from app.core.security import require_api_key

router = APIRouter(prefix="/skills", tags=["skills"])


def _container(request: Request):
    return request.app.state.container


@router.get("")
async def list_skills(request: Request, _: None = Depends(require_api_key)):
    c = _container(request)
    return {"items": await c.skill_store.list_skills()}


@router.post("")
async def create_skill(
    body: SkillCreate,
    request: Request,
    _: None = Depends(require_api_key),
):
    c = _container(request)
    try:
        created = await c.skill_store.create(
            name=body.name,
            description=body.description,
            body=body.body,
            tools=body.tools,
            mcp=body.mcp,
            enabled=body.enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await c.capability_sync.sync_skills()
    return created


@router.get("/{name}")
async def get_skill(name: str, request: Request, _: None = Depends(require_api_key)):
    c = _container(request)
    item = await c.skill_store.get(name)
    if item is None:
        raise HTTPException(status_code=404, detail="skill not found")
    return item


@router.put("/{name}")
async def update_skill(
    name: str,
    body: SkillUpdate,
    request: Request,
    _: None = Depends(require_api_key),
):
    c = _container(request)
    fields = body.model_dump(exclude_unset=True)
    try:
        updated = await c.skill_store.update(name, **fields)
    except KeyError:
        raise HTTPException(status_code=404, detail="skill not found") from None
    await c.capability_sync.sync_skills()
    return updated


@router.delete("/{name}")
async def delete_skill(name: str, request: Request, _: None = Depends(require_api_key)):
    c = _container(request)
    try:
        await c.skill_store.delete(name)
    except KeyError:
        raise HTTPException(status_code=404, detail="skill not found") from None
    await c.capability_sync.sync_skills()
    return {"ok": True, "name": name}


@router.patch("/{name}/enabled")
async def patch_skill_enabled(
    name: str,
    body: EnabledPatch,
    request: Request,
    _: None = Depends(require_api_key),
):
    c = _container(request)
    try:
        updated = await c.skill_store.set_enabled(name, body.enabled)
    except KeyError:
        raise HTTPException(status_code=404, detail="skill not found") from None
    await c.capability_sync.sync_skills()
    return updated
