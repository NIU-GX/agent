"""Langfuse 全链路追踪：可选 CallbackHandler，关闭时零开销。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from shared.config import settings
from shared.logging import get_logger

logger = get_logger(__name__)


@dataclass
class TraceContext:
    """单次 agent.run 的追踪上下文。"""

    enabled: bool
    trace_id: str
    langfuse_url: str | None
    handler: Any | None = None

    def callbacks(self) -> list[Any]:
        return [self.handler] if self.handler is not None else []

    def flush(self) -> None:
        if self.handler is None:
            return
        try:
            flush = getattr(self.handler, "flush", None)
            if callable(flush):
                flush()
                return
            client = getattr(self.handler, "client", None) or getattr(self.handler, "langfuse", None)
            if client is not None and hasattr(client, "flush"):
                client.flush()
        except Exception as exc:  # noqa: BLE001
            logger.debug("langfuse flush failed: %s", exc)


def langfuse_ready() -> bool:
    return bool(
        settings.langfuse_enabled
        and settings.langfuse_public_key
        and settings.langfuse_secret_key
        and settings.langfuse_host
    )


def _build_handler() -> Any | None:
    if not langfuse_ready():
        return None
    try:
        try:
            from langfuse.langchain import CallbackHandler  # type: ignore[import-untyped]
        except ImportError:
            from langfuse.callback import CallbackHandler  # type: ignore[import-untyped]
    except ImportError:
        logger.warning("langfuse package not installed; tracing disabled")
        return None

    kwargs: dict[str, Any] = {
        "public_key": settings.langfuse_public_key,
        "secret_key": settings.langfuse_secret_key,
        "host": settings.langfuse_host.rstrip("/"),
    }
    try:
        return CallbackHandler(**kwargs)
    except TypeError:
        # 部分版本仅读环境变量
        return CallbackHandler()
    except Exception as exc:  # noqa: BLE001
        logger.warning("langfuse handler init failed: %s", exc)
        return None


def _resolve_trace_id(handler: Any | None, fallback: str) -> str:
    if handler is None:
        return fallback
    for attr in ("get_trace_id", "get_traceid"):
        fn = getattr(handler, attr, None)
        if callable(fn):
            try:
                tid = fn()
                if tid:
                    return str(tid)
            except Exception:  # noqa: BLE001
                pass
    for attr in ("trace_id", "last_trace_id"):
        tid = getattr(handler, attr, None)
        if tid:
            return str(tid)
    return fallback


def _trace_url(trace_id: str) -> str | None:
    base = (settings.langfuse_public_url or settings.langfuse_host or "").rstrip("/")
    if not base or not trace_id:
        return None
    return f"{base}/trace/{trace_id}"


def start_trace(
    *,
    session_id: str,
    strategy: str,
    skills: list[str] | None = None,
    enable_rag: bool = True,
    name: str = "agent.run",
) -> TraceContext:
    """创建本次 run 的 TraceContext；未启用时返回 disabled stub。"""
    fallback_id = str(uuid4())
    handler = _build_handler()
    if handler is None:
        return TraceContext(enabled=False, trace_id=fallback_id, langfuse_url=None, handler=None)

    # 尽量设置会话与元数据（SDK 版本差异做容错）
    try:
        if hasattr(handler, "session_id"):
            handler.session_id = session_id
        meta = {
            "strategy": strategy,
            "skills": list(skills or []),
            "enable_rag": enable_rag,
            "run_name": name,
        }
        if hasattr(handler, "metadata") and isinstance(getattr(handler, "metadata", None), dict):
            handler.metadata.update(meta)
        elif hasattr(handler, "tags"):
            tags = list(getattr(handler, "tags") or [])
            tags.append(f"strategy:{strategy}")
            handler.tags = tags
    except Exception as exc:  # noqa: BLE001
        logger.debug("langfuse metadata attach skipped: %s", exc)

    trace_id = _resolve_trace_id(handler, fallback_id)
    return TraceContext(
        enabled=True,
        trace_id=trace_id,
        langfuse_url=_trace_url(trace_id),
        handler=handler,
    )


def refresh_trace_ids(ctx: TraceContext) -> TraceContext:
    """astream 结束后再解析一次 trace_id（部分 SDK 延迟分配）。"""
    if not ctx.enabled or ctx.handler is None:
        return ctx
    tid = _resolve_trace_id(ctx.handler, ctx.trace_id)
    return TraceContext(
        enabled=True,
        trace_id=tid,
        langfuse_url=_trace_url(tid),
        handler=ctx.handler,
    )
