from __future__ import annotations

"""健康检查：供 K8s probe / Compose healthcheck 使用。"""

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict:
    container = getattr(request.app.state, "container", None)
    return {
        "status": "ok",
        "rabbitmq": bool(container and container.publisher),
    }
