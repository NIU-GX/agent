"""API Key 鉴权：非 dev 环境强制校验。"""

from __future__ import annotations

from fastapi import Header, HTTPException

from shared.config import settings


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    if settings.app_env != "dev":
        if not x_api_key or x_api_key != settings.app_api_key:
            raise HTTPException(status_code=401, detail="invalid api key")
        return
    if x_api_key is not None and x_api_key != settings.app_api_key:
        raise HTTPException(status_code=401, detail="invalid api key")
