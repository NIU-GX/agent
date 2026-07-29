from __future__ import annotations

"""v1 路由聚合。"""

from fastapi import APIRouter

from app.api.v1 import (
    capabilities,
    chat,
    documents,
    eval_api,
    health,
    knowledge_bases,
    mcp_servers,
    metrics,
    prompts,
    skills,
    tools,
)

router = APIRouter()
router.include_router(health.router, tags=["health"])
router.include_router(chat.router, tags=["chat"])
router.include_router(documents.router, tags=["documents"])
router.include_router(knowledge_bases.router, tags=["knowledge-bases"])
router.include_router(eval_api.router, tags=["eval"])
router.include_router(metrics.router, tags=["metrics"])
router.include_router(capabilities.router, tags=["capabilities"])
router.include_router(prompts.router)
router.include_router(tools.router)
router.include_router(skills.router)
router.include_router(mcp_servers.router)
