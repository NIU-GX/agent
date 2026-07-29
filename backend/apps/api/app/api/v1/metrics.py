"""用量指标：优先 Postgres 聚合，附带进程内近期明细。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.core.security import require_api_key

router = APIRouter()


@router.get("/metrics/usage")
async def usage_metrics(request: Request, _: None = Depends(require_api_key)):
    llm = request.app.state.container.llm
    recorder = llm.usage_recorder
    summary = recorder.summary()
    if hasattr(recorder, "summary_from_db"):
        try:
            summary = await recorder.summary_from_db()
        except Exception:  # noqa: BLE001
            pass
    return {
        "summary": summary,
        "recent": [r.model_dump() for r in recorder.list_recent(50)],
    }


@router.get("/metrics/rag")
async def rag_metrics(request: Request, _: None = Depends(require_api_key)):
    """供 Prometheus exporter 或平台监控轮询的 RAG 控制面指标。"""
    return await request.app.state.container.rag_store.rag_metrics()
