"""Webhook / 内置工具管理：列表、创建、更新、删除、启用。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from shared.schemas import EnabledPatch, WebhookToolCreate, WebhookToolUpdate

from app.core.security import require_api_key

router = APIRouter(prefix="/tools", tags=["tools"])


def _container(request: Request):
    return request.app.state.container


@router.get("")
async def list_tools(request: Request, _: None = Depends(require_api_key)):
    """合并 runtime 内置/元工具与 Store webhook；含 disabled。"""
    c = _container(request)
    items = c.tools.admin_catalog()
    # 补充 Store 中尚未 sync 成功的 webhook（兜底）
    store_items = {t["name"]: t for t in await c.tool_store.list_tools()}
    for name, rec in store_items.items():
        if not any(i["name"] == name for i in items):
            items.append(rec)
    items.sort(key=lambda x: x["name"])
    return {"items": items}


@router.post("")
async def create_tool(
    body: WebhookToolCreate,
    request: Request,
    _: None = Depends(require_api_key),
):
    c = _container(request)
    # 禁止覆盖内置名
    existing_runtime = {i["name"] for i in c.tools.admin_catalog()}
    if body.name in existing_runtime and body.name not in {
        t["name"] for t in await c.tool_store.list_tools()
    }:
        raise HTTPException(status_code=400, detail=f"name reserved by builtin: {body.name}")
    try:
        created = await c.tool_store.create(
            name=body.name,
            description=body.description,
            parameters=body.parameters,
            webhook_url=body.webhook_url,
            webhook_method=body.webhook_method,
            webhook_headers=body.webhook_headers,
            timeout_sec=body.timeout_sec,
            tier=body.tier,
            enabled=body.enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await c.capability_sync.sync_tools()
    return created


@router.get("/{name}")
async def get_tool(name: str, request: Request, _: None = Depends(require_api_key)):
    c = _container(request)
    for item in c.tools.admin_catalog():
        if item["name"] == name:
            if item.get("source") == "webhook":
                stored = await c.tool_store.get(name)
                return stored or item
            return item
    stored = await c.tool_store.get(name)
    if stored:
        return stored
    raise HTTPException(status_code=404, detail="tool not found")


@router.put("/{name}")
async def update_tool(
    name: str,
    body: WebhookToolUpdate,
    request: Request,
    _: None = Depends(require_api_key),
):
    c = _container(request)
    stored = await c.tool_store.get(name)
    if stored is None:
        raise HTTPException(status_code=404, detail="webhook tool not found (builtins are read-only)")
    fields = body.model_dump(exclude_unset=True)
    try:
        updated = await c.tool_store.update(name, **fields)
    except KeyError:
        raise HTTPException(status_code=404, detail="tool not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await c.capability_sync.sync_tools()
    return updated


@router.delete("/{name}")
async def delete_tool(name: str, request: Request, _: None = Depends(require_api_key)):
    c = _container(request)
    stored = await c.tool_store.get(name)
    if stored is None:
        raise HTTPException(status_code=400, detail="only webhook tools can be deleted")
    try:
        await c.tool_store.delete(name)
    except KeyError:
        raise HTTPException(status_code=404, detail="tool not found") from None
    await c.capability_sync.sync_tools()
    return {"ok": True, "name": name}


@router.patch("/{name}/enabled")
async def patch_tool_enabled(
    name: str,
    body: EnabledPatch,
    request: Request,
    _: None = Depends(require_api_key),
):
    c = _container(request)
    stored = await c.tool_store.get(name)
    if stored is not None:
        updated = await c.tool_store.set_enabled(name, body.enabled)
        await c.capability_sync.sync_tools()
        return updated
    # 内置 / 元工具：写 flag
    runtime_names = {i["name"] for i in c.tools.admin_catalog()}
    if name not in runtime_names:
        raise HTTPException(status_code=404, detail="tool not found")
    result = await c.tool_store.set_flag(name, body.enabled)
    await c.capability_sync.sync_tools()
    return result
