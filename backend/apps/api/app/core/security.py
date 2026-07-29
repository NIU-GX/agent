"""API Key 鉴权：非 dev 环境强制校验。"""

from __future__ import annotations

from fastapi import Header, HTTPException, Request

from shared.rag_store import Principal


async def require_principal(
    request: Request, x_api_key: str | None = Header(default=None)
) -> Principal:
    if not x_api_key:
        raise HTTPException(status_code=401, detail="missing api key")
    principal = await request.app.state.container.rag_store.authenticate(x_api_key)
    if principal is None:
        raise HTTPException(status_code=401, detail="invalid api key")
    return principal


async def require_api_key(request: Request, x_api_key: str | None = Header(default=None)) -> None:
    await require_principal(request, x_api_key)
