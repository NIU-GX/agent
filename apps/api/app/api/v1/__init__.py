from __future__ import annotations

"""v1 路由聚合。"""

from fastapi import APIRouter

from app.api.v1 import capabilities, chat, documents, eval_api, health, metrics

router = APIRouter()
router.include_router(health.router, tags=["health"])
router.include_router(chat.router, tags=["chat"])
router.include_router(documents.router, tags=["documents"])
router.include_router(eval_api.router, tags=["eval"])
router.include_router(metrics.router, tags=["metrics"])
router.include_router(capabilities.router, tags=["capabilities"])
