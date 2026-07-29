"""FastAPI 应用入口：组装网关、Agent、RAG 发布依赖。"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from shared.logging import get_logger

from app.api.v1 import router as v1_router
from app.core.deps import AppState, get_app_state

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化连接；关闭时释放。"""
    state = await get_app_state()
    app.state.container = state
    logger.info("api started")
    yield
    await state.aclose()
    logger.info("api stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Agent Platform API",
        version="0.1.0",
        description="企业级 Agent：LLM 网关 / RAG / CoT·ReAct·Plan-Execute / Eval",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(v1_router, prefix="/api/v1")
    return app


app = create_app()
