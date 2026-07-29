"""Postgres 元数据层：文档 / Job / 用量 / 幂等记录。

API 与 rag-worker 共享同一库，保证入库状态跨进程可见。
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from shared.config import settings
from shared.logging import get_logger

logger = get_logger(__name__)


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DocumentRow(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="uploaded")
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    blob_key: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    content_hash: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class JobRow(Base):
    __tablename__ = "ingest_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class UsageRow(Base):
    __tablename__ = "llm_usage"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid4()))
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    request_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class IdempotencyRow(Base):
    """doc_id + stage + content_hash 幂等键，避免重复入库。"""

    __tablename__ = "ingest_idempotency"
    __table_args__ = (UniqueConstraint("doc_id", "stage", "content_hash", name="uq_idem"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid4()))
    doc_id: Mapped[str] = mapped_column(String(64), nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Database:
    """异步引擎与会话工厂。"""

    def __init__(self, dsn: Optional[str] = None) -> None:
        self.dsn = dsn or settings.postgres_dsn
        kwargs: Dict[str, Any] = {"pool_pre_ping": True}
        # SQLite（单测）不支持 QueuePool 的 pool_size
        if not self.dsn.startswith("sqlite"):
            kwargs["pool_size"] = 5
        self.engine = create_async_engine(self.dsn, **kwargs)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def create_tables(self) -> None:
        # 确保扩展模型已注册到同一 Base.metadata
        import shared.mcp_store  # noqa: F401
        import shared.prompt_store  # noqa: F401
        import shared.rag_store  # noqa: F401
        import shared.skill_store  # noqa: F401
        import shared.tool_store  # noqa: F401

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("postgres schema ready")

    async def ensure_schema(self) -> None:
        """开发环境建表；生产只允许由 Alembic 迁移管理 schema。"""
        if settings.app_env == "prod" and not settings.auto_create_schema:
            logger.info("schema auto-create disabled; expecting Alembic-managed schema")
            return
        await self.create_tables()

    async def aclose(self) -> None:
        await self.engine.dispose()

    def session(self) -> AsyncSession:
        return self.session_factory()


class PostgresStatusStore:
    """文档 / Job 状态读写，供 API 与 Worker 共用。"""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def create_document(
        self,
        *,
        doc_id: str,
        filename: str,
        blob_key: str,
        status: str = "uploaded",
    ) -> Dict[str, Any]:
        now = _utcnow()
        async with self.db.session() as session:
            row = DocumentRow(
                id=doc_id,
                filename=filename,
                status=status,
                blob_key=blob_key,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            await session.commit()
            return _doc_to_dict(row)

    async def create_job(
        self,
        *,
        job_id: str,
        doc_id: str,
        stage: str,
        status: str = "queued",
    ) -> Dict[str, Any]:
        now = _utcnow()
        async with self.db.session() as session:
            row = JobRow(
                id=job_id,
                doc_id=doc_id,
                stage=stage,
                status=status,
                updated_at=now,
            )
            session.add(row)
            await session.commit()
            return _job_to_dict(row)

    async def set_document_status(
        self,
        doc_id: str,
        status: str,
        *,
        error: Optional[str] = None,
        chunk_count: Optional[int] = None,
        content_hash: Optional[str] = None,
    ) -> None:
        async with self.db.session() as session:
            row = await session.get(DocumentRow, doc_id)
            if row is None:
                row = DocumentRow(id=doc_id, filename="unknown", status=status)
                session.add(row)
            row.status = status
            row.updated_at = _utcnow()
            if error is not None:
                row.error_message = error
            if chunk_count is not None:
                row.chunk_count = chunk_count
            if content_hash is not None:
                row.content_hash = content_hash
            await session.commit()

    async def set_job_stage(
        self,
        job_id: str,
        stage: str,
        status: str,
        *,
        error: Optional[str] = None,
        attempt: int = 0,
    ) -> None:
        async with self.db.session() as session:
            row = await session.get(JobRow, job_id)
            if row is None:
                row = JobRow(id=job_id, doc_id="", stage=stage, status=status)
                session.add(row)
            row.stage = stage
            row.status = status
            row.attempt = attempt
            row.updated_at = _utcnow()
            if error is not None:
                row.error_message = error
            await session.commit()

    async def list_documents(self, limit: int = 200) -> List[Dict[str, Any]]:
        async with self.db.session() as session:
            result = await session.execute(
                select(DocumentRow).order_by(DocumentRow.created_at.desc()).limit(limit)
            )
            return [_doc_to_dict(r) for r in result.scalars().all()]

    async def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        async with self.db.session() as session:
            row = await session.get(JobRow, job_id)
            return _job_to_dict(row) if row else None

    async def claim_idempotency(self, doc_id: str, stage: str, content_hash: str) -> bool:
        """返回 True 表示首次处理；False 表示已处理过可跳过。"""
        if not content_hash:
            return True
        async with self.db.session() as session:
            existing = await session.execute(
                select(IdempotencyRow).where(
                    IdempotencyRow.doc_id == doc_id,
                    IdempotencyRow.stage == stage,
                    IdempotencyRow.content_hash == content_hash,
                )
            )
            if existing.scalar_one_or_none():
                return False
            session.add(
                IdempotencyRow(doc_id=doc_id, stage=stage, content_hash=content_hash)
            )
            try:
                await session.commit()
                return True
            except Exception:  # noqa: BLE001 — 并发唯一约束冲突视为已处理
                await session.rollback()
                return False


class PostgresUsageRecorder:
    """用量落库 + 进程内近期缓存，供看板查询。"""

    def __init__(self, db: Database) -> None:
        self.db = db
        self._recent: List[Any] = []

    async def record(self, record: Any) -> None:
        from shared.pricing import estimate_cost_usd
        from shared.schemas import UsageRecord

        cost = estimate_cost_usd(
            record.model,
            prompt_tokens=getattr(record, "prompt_tokens", 0),
            completion_tokens=getattr(record, "completion_tokens", 0),
        )
        if isinstance(record, UsageRecord):
            record = record.model_copy(update={"cost_usd": cost})
        else:
            try:
                record.cost_usd = cost
            except Exception:  # noqa: BLE001
                pass

        self._recent.append(record)
        self._recent = self._recent[-500:]
        async with self.db.session() as session:
            session.add(
                UsageRow(
                    model=record.model,
                    prompt_tokens=getattr(record, "prompt_tokens", 0),
                    completion_tokens=getattr(record, "completion_tokens", 0),
                    total_tokens=getattr(record, "total_tokens", 0),
                    cost_usd=cost,
                    request_id=getattr(record, "request_id", None),
                )
            )
            await session.commit()

    def list_recent(self, limit: int = 100) -> List[Any]:
        return self._recent[-limit:]

    def summary(self) -> Dict[str, Union[int, float]]:
        return {
            "calls": len(self._recent),
            "total_tokens": sum(getattr(r, "total_tokens", 0) for r in self._recent),
            "cost_usd": round(sum(getattr(r, "cost_usd", 0.0) for r in self._recent), 6),
        }

    async def summary_from_db(self) -> Dict[str, Union[int, float]]:
        async with self.db.session() as session:
            rows = (await session.execute(select(UsageRow))).scalars().all()
            return {
                "calls": len(rows),
                "total_tokens": sum(r.total_tokens for r in rows),
                "cost_usd": round(sum(r.cost_usd for r in rows), 6),
            }


def _doc_to_dict(row: DocumentRow) -> Dict[str, Any]:
    return {
        "id": row.id,
        "filename": row.filename,
        "status": row.status,
        "error_message": row.error_message,
        "chunk_count": row.chunk_count,
        "blob_key": row.blob_key,
        "content_hash": row.content_hash,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _job_to_dict(row: JobRow) -> Dict[str, Any]:
    return {
        "id": row.id,
        "doc_id": row.doc_id,
        "stage": row.stage,
        "status": row.status,
        "attempt": row.attempt,
        "error_message": row.error_message,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
