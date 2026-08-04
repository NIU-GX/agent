"""Agent Run 指针落库：session/run → Langfuse trace；不存工具轨迹正文。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from sqlalchemy import DateTime, String, select
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import Base, Database
from shared.logging import get_logger

logger = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AgentRunRow(Base):
    """业务侧 run 指针；完整 LLM/tool span 以 Langfuse 为准。"""

    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid4()))
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    trace_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    langfuse_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    strategy: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="started")
    tenant_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


def _row_to_dict(row: AgentRunRow) -> Dict[str, Any]:
    return {
        "run_id": row.id,
        "session_id": row.session_id,
        "trace_id": row.trace_id,
        "langfuse_url": row.langfuse_url,
        "strategy": row.strategy,
        "status": row.status,
        "tenant_id": row.tenant_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


class AgentRunStore:
    """只持久化指针字段；禁止写入 tool args/results。"""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def upsert(
        self,
        *,
        run_id: str,
        session_id: str,
        trace_id: str | None = None,
        langfuse_url: str | None = None,
        strategy: str | None = None,
        status: str = "started",
        tenant_id: str | None = None,
    ) -> Dict[str, Any]:
        now = _utcnow()
        async with self.db.session() as session:
            row = await session.get(AgentRunRow, run_id)
            if row is None:
                row = AgentRunRow(
                    id=run_id,
                    session_id=session_id,
                    trace_id=trace_id,
                    langfuse_url=langfuse_url,
                    strategy=strategy,
                    status=status,
                    tenant_id=tenant_id,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.session_id = session_id or row.session_id
                if trace_id is not None:
                    row.trace_id = trace_id
                if langfuse_url is not None:
                    row.langfuse_url = langfuse_url
                if strategy is not None:
                    row.strategy = strategy
                if status:
                    row.status = status
                if tenant_id is not None:
                    row.tenant_id = tenant_id
                row.updated_at = now
            await session.commit()
            await session.refresh(row)
            return _row_to_dict(row)

    async def get(self, run_id: str) -> Optional[Dict[str, Any]]:
        async with self.db.session() as session:
            row = await session.get(AgentRunRow, run_id)
            return _row_to_dict(row) if row else None

    async def list_by_session(
        self,
        session_id: str,
        *,
        limit: int = 50,
        tenant_id: str | None = None,
    ) -> List[Dict[str, Any]]:
        async with self.db.session() as session:
            stmt = (
                select(AgentRunRow)
                .where(AgentRunRow.session_id == session_id)
                .order_by(AgentRunRow.created_at.desc())
                .limit(limit)
            )
            if tenant_id:
                stmt = stmt.where(AgentRunRow.tenant_id == tenant_id)
            rows = (await session.execute(stmt)).scalars().all()
            return [_row_to_dict(r) for r in rows]
