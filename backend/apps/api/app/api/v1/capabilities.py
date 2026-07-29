"""能力发现 API：Tool / Skill / MCP 的 L0（及可选 L1）目录。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from app.core.security import require_api_key

router = APIRouter(prefix="/capabilities")


@router.get("/tools")
async def list_tools(request: Request, _: None = Depends(require_api_key)):
    container = request.app.state.container
    return {"items": container.tools.catalog(unlocked=set())}


@router.get("/skills")
async def list_skills(
    request: Request,
    name: str | None = Query(default=None, description="指定 name 时返回 L1 正文"),
    _: None = Depends(require_api_key),
):
    container = request.app.state.container
    skills = container.skills
    if name:
        result = skills.activate(name)
        if not result.get("ok"):
            return {"ok": False, "error": result.get("error"), "item": None}
        return {
            "ok": True,
            "item": {
                "name": result["name"],
                "description": result.get("description"),
                "body": result.get("body"),
                "tools": result.get("tools"),
                "mcp": result.get("mcp"),
            },
        }
    return {"items": skills.catalog()}


@router.get("/mcp")
async def list_mcp(request: Request, _: None = Depends(require_api_key)):
    container = request.app.state.container
    return {"items": container.tools.mcp.catalog()}
