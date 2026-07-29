"""MCP Server 管理：列表、创建、更新、删除、启用、重连。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from shared.schemas import EnabledPatch, McpServerCreate, McpServerUpdate

from app.core.security import require_api_key

router = APIRouter(prefix="/mcp", tags=["mcp"])


def _container(request: Request):
    return request.app.state.container


def _enrich(server: dict, runtime_catalog: list[dict]) -> dict:
    """合并 Store 配置与 runtime 已发现 tools / error。"""
    out = dict(server)
    match = next((s for s in runtime_catalog if s.get("name") == server.get("name")), None)
    if match:
        out["tools"] = match.get("tools") or []
        if match.get("error") and not out.get("last_error"):
            out["last_error"] = match.get("error")
    else:
        out.setdefault("tools", [])
    return out


@router.get("")
async def list_mcp(request: Request, _: None = Depends(require_api_key)):
    c = _container(request)
    runtime = c.tools.mcp.catalog()
    items = [_enrich(s, runtime) for s in await c.mcp_store.list_servers()]
    return {"items": items}


@router.post("")
async def create_mcp(
    body: McpServerCreate,
    request: Request,
    _: None = Depends(require_api_key),
):
    c = _container(request)
    try:
        created = await c.mcp_store.create(
            name=body.name,
            command=body.command,
            args=body.args,
            env=body.env,
            enabled=body.enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await c.capability_sync.sync_mcp()
    runtime = c.tools.mcp.catalog()
    refreshed = await c.mcp_store.get(body.name)
    return _enrich(refreshed or created, runtime)


@router.get("/{name}")
async def get_mcp(name: str, request: Request, _: None = Depends(require_api_key)):
    c = _container(request)
    item = await c.mcp_store.get(name)
    if item is None:
        raise HTTPException(status_code=404, detail="mcp server not found")
    return _enrich(item, c.tools.mcp.catalog())


@router.put("/{name}")
async def update_mcp(
    name: str,
    body: McpServerUpdate,
    request: Request,
    _: None = Depends(require_api_key),
):
    c = _container(request)
    fields = body.model_dump(exclude_unset=True)
    try:
        updated = await c.mcp_store.update(name, **fields)
    except KeyError:
        raise HTTPException(status_code=404, detail="mcp server not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await c.capability_sync.sync_mcp()
    refreshed = await c.mcp_store.get(name)
    return _enrich(refreshed or updated, c.tools.mcp.catalog())


@router.delete("/{name}")
async def delete_mcp(name: str, request: Request, _: None = Depends(require_api_key)):
    c = _container(request)
    try:
        await c.mcp_store.delete(name)
    except KeyError:
        raise HTTPException(status_code=404, detail="mcp server not found") from None
    await c.capability_sync.sync_mcp()
    return {"ok": True, "name": name}


@router.patch("/{name}/enabled")
async def patch_mcp_enabled(
    name: str,
    body: EnabledPatch,
    request: Request,
    _: None = Depends(require_api_key),
):
    c = _container(request)
    try:
        updated = await c.mcp_store.set_enabled(name, body.enabled)
    except KeyError:
        raise HTTPException(status_code=404, detail="mcp server not found") from None
    await c.capability_sync.sync_mcp()
    refreshed = await c.mcp_store.get(name)
    return _enrich(refreshed or updated, c.tools.mcp.catalog())


@router.post("/{name}/reconnect")
async def reconnect_mcp(name: str, request: Request, _: None = Depends(require_api_key)):
    c = _container(request)
    item = await c.mcp_store.get(name)
    if item is None:
        raise HTTPException(status_code=404, detail="mcp server not found")
    await c.capability_sync.sync_mcp()
    refreshed = await c.mcp_store.get(name)
    runtime = c.tools.mcp.catalog()
    enriched = _enrich(refreshed or item, runtime)
    return {
        "ok": not bool(enriched.get("last_error")),
        "name": name,
        "error": enriched.get("last_error"),
        "server": enriched,
    }
