"""生产 RAG 元数据、授权、Outbox 与可恢复阶段状态机。"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from shared.config import settings
from shared.db import Base, Database, DocumentRow, _utcnow
from shared.schemas import DocumentStatus, RagStage


class TenantRow(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=_utcnow)


class KnowledgeBaseRow(Base):
    __tablename__ = "knowledge_bases"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ApiPrincipalRow(Base):
    __tablename__ = "api_principals"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    key_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    role: Mapped[str] = mapped_column(String(32), default="reader")
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=_utcnow)


class KnowledgeBaseMembershipRow(Base):
    __tablename__ = "knowledge_base_memberships"
    __table_args__ = (UniqueConstraint("principal_id", "kb_id", name="uq_kb_membership"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid4()))
    principal_id: Mapped[str] = mapped_column(String(64), index=True)
    kb_id: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str] = mapped_column(String(32), default="reader")


class DocumentVersionRow(Base):
    __tablename__ = "document_versions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(64), index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    kb_id: Mapped[str] = mapped_column(String(64), index=True)
    blob_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="uploaded")
    active: Mapped[bool] = mapped_column(default=False, index=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=_utcnow)


class IngestStageRunRow(Base):
    __tablename__ = "ingest_stage_runs"
    __table_args__ = (UniqueConstraint("document_version_id", "stage", name="uq_version_stage"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(64), index=True)
    document_version_id: Mapped[str] = mapped_column(String(64), index=True)
    stage: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    payload_ref: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_until: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=_utcnow)


class OutboxEventRow(Base):
    __tablename__ = "outbox_events"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    topic: Mapped[str] = mapped_column(String(128), index=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=_utcnow)
    published_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ParentChunkRow(Base):
    __tablename__ = "parent_chunks"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_version_id: Mapped[str] = mapped_column(String(64), index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(512), default="")


def hash_api_key(value: str) -> str:
    return hashlib.sha256(f"{settings.api_key_pepper}:{value}".encode()).hexdigest()


class Principal:
    def __init__(self, *, id: str, tenant_id: str, role: str, kb_ids: set[str]) -> None:
        self.id = id
        self.tenant_id = tenant_id
        self.role = role
        self.kb_ids = kb_ids

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class ProductionRagStore:
    """授权和入库状态仅在此层改变，保证跨 API/worker 一致。"""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def bootstrap_legacy(self) -> None:
        """平滑迁移：仅在尚无 principal 时创建 legacy tenant/KB/admin。"""
        async with self.db.session() as session:
            tenant = await session.get(TenantRow, settings.legacy_tenant_id)
            if tenant is None:
                session.add(TenantRow(id=settings.legacy_tenant_id, name="Legacy tenant"))
            kb = await session.get(KnowledgeBaseRow, settings.legacy_knowledge_base_id)
            if kb is None:
                session.add(
                    KnowledgeBaseRow(
                        id=settings.legacy_knowledge_base_id,
                        tenant_id=settings.legacy_tenant_id,
                        name="Legacy knowledge base",
                    )
                )
            key_hash = hash_api_key(settings.app_api_key)
            principal = (
                await session.execute(select(ApiPrincipalRow).where(ApiPrincipalRow.key_hash == key_hash))
            ).scalar_one_or_none()
            if principal is None:
                principal = ApiPrincipalRow(
                    id="bootstrap-admin",
                    tenant_id=settings.legacy_tenant_id,
                    key_hash=key_hash,
                    role="admin",
                )
                session.add(principal)
                session.add(
                    KnowledgeBaseMembershipRow(
                        principal_id=principal.id,
                        kb_id=settings.legacy_knowledge_base_id,
                        role="admin",
                    )
                )
            await session.commit()

    async def authenticate(self, api_key: str) -> Principal | None:
        digest = hash_api_key(api_key)
        async with self.db.session() as session:
            principal = (
                await session.execute(
                    select(ApiPrincipalRow).where(
                        ApiPrincipalRow.key_hash == digest, ApiPrincipalRow.enabled.is_(True)
                    )
                )
            ).scalar_one_or_none()
            if principal is None or not hmac.compare_digest(principal.key_hash, digest):
                return None
            memberships = await session.execute(
                select(KnowledgeBaseMembershipRow.kb_id).where(
                    KnowledgeBaseMembershipRow.principal_id == principal.id
                )
            )
            return Principal(
                id=principal.id,
                tenant_id=principal.tenant_id,
                role=principal.role,
                kb_ids=set(memberships.scalars().all()),
            )

    async def create_knowledge_base(self, principal: Principal, name: str) -> dict[str, str]:
        if not principal.is_admin:
            raise PermissionError("knowledge base creation requires admin")
        row = KnowledgeBaseRow(id=str(uuid4()), tenant_id=principal.tenant_id, name=name)
        async with self.db.session() as session:
            session.add(row)
            session.add(KnowledgeBaseMembershipRow(principal_id=principal.id, kb_id=row.id, role="admin"))
            await session.commit()
        return {"id": row.id, "tenant_id": row.tenant_id, "name": row.name, "status": row.status}

    async def update_knowledge_base(self, principal: Principal, kb_id: str, name: str) -> dict[str, str]:
        if not principal.is_admin:
            raise PermissionError("knowledge base update requires admin")
        async with self.db.session() as session:
            row = await session.get(KnowledgeBaseRow, kb_id, with_for_update=True)
            if row is None or row.tenant_id != principal.tenant_id:
                raise PermissionError("knowledge base access denied")
            row.name = name
            await session.commit()
            return {"id": row.id, "tenant_id": row.tenant_id, "name": row.name, "status": row.status}

    async def archive_knowledge_base(self, principal: Principal, kb_id: str) -> None:
        if not principal.is_admin:
            raise PermissionError("knowledge base deletion requires admin")
        async with self.db.session() as session:
            row = await session.get(KnowledgeBaseRow, kb_id, with_for_update=True)
            if row is None or row.tenant_id != principal.tenant_id:
                raise PermissionError("knowledge base access denied")
            row.status = "archived"
            await session.commit()

    async def list_knowledge_bases(self, principal: Principal) -> list[dict[str, str]]:
        async with self.db.session() as session:
            stmt = select(KnowledgeBaseRow).where(KnowledgeBaseRow.tenant_id == principal.tenant_id)
            if not principal.is_admin:
                stmt = stmt.where(KnowledgeBaseRow.id.in_(principal.kb_ids or {"__none__"}))
            rows = (await session.execute(stmt.order_by(KnowledgeBaseRow.name))).scalars().all()
            return [{"id": row.id, "tenant_id": row.tenant_id, "name": row.name, "status": row.status} for row in rows]

    async def active_version_ids(self, tenant_id: str, kb_ids: set[str]) -> set[str]:
        if not kb_ids:
            return set()
        async with self.db.session() as session:
            rows = await session.execute(
                select(DocumentVersionRow.id).where(
                    DocumentVersionRow.tenant_id == tenant_id,
                    DocumentVersionRow.kb_id.in_(kb_ids),
                    DocumentVersionRow.active.is_(True),
                    DocumentVersionRow.status == DocumentStatus.READY.value,
                )
            )
            return set(rows.scalars().all())

    async def list_documents_for_scope(self, principal: Principal, kb_ids: set[str]) -> list[dict[str, Any]]:
        permitted = kb_ids if principal.is_admin else kb_ids & principal.kb_ids
        if not permitted:
            return []
        async with self.db.session() as session:
            rows = await session.execute(
                select(DocumentVersionRow, DocumentRow)
                .join(DocumentRow, DocumentVersionRow.document_id == DocumentRow.id)
                .where(
                    DocumentVersionRow.tenant_id == principal.tenant_id,
                    DocumentVersionRow.kb_id.in_(permitted),
                    DocumentVersionRow.active.is_(True),
                )
                .order_by(DocumentVersionRow.created_at.desc())
            )
            return [
                {
                    "id": doc.id,
                    "version_id": version.id,
                    "kb_id": version.kb_id,
                    "filename": doc.filename,
                    "status": version.status,
                    "chunk_count": doc.chunk_count,
                    "created_at": doc.created_at.isoformat(),
                }
                for version, doc in rows
            ]

    async def ensure_kb_access(self, principal: Principal, kb_id: str, *, write: bool = False) -> None:
        if not principal.is_admin and kb_id not in principal.kb_ids:
            raise PermissionError("knowledge base access denied")
        async with self.db.session() as session:
            kb = await session.get(KnowledgeBaseRow, kb_id)
            if kb is None or kb.tenant_id != principal.tenant_id or kb.status != "active":
                raise PermissionError("knowledge base access denied")
            if write and not principal.is_admin:
                membership = (
                    await session.execute(
                        select(KnowledgeBaseMembershipRow).where(
                            KnowledgeBaseMembershipRow.principal_id == principal.id,
                            KnowledgeBaseMembershipRow.kb_id == kb_id,
                        )
                    )
                ).scalar_one_or_none()
                if membership is None or membership.role not in {"writer", "admin"}:
                    raise PermissionError("knowledge base write denied")

    async def create_document_with_outbox(
        self, *, tenant_id: str, kb_id: str, filename: str, blob_key: str
    ) -> dict[str, str]:
        doc_id, version_id, run_id, event_id = (str(uuid4()) for _ in range(4))
        payload = {
            "job_id": run_id,
            "doc_id": doc_id,
            "document_version_id": version_id,
            "stage": RagStage.PARSE.value,
            "payload_ref": blob_key,
            "attempt": 0,
            "tenant_id": tenant_id,
            "kb_id": kb_id,
            "outbox_event_id": event_id,
        }
        async with self.db.session() as session:
            session.add(
                DocumentRow(
                    id=doc_id,
                    filename=filename,
                    status=DocumentStatus.UPLOADED.value,
                    blob_key=blob_key,
                )
            )
            session.add(
                DocumentVersionRow(
                    id=version_id,
                    document_id=doc_id,
                    tenant_id=tenant_id,
                    kb_id=kb_id,
                    blob_key=blob_key,
                )
            )
            session.add(
                IngestStageRunRow(
                    id=run_id,
                    document_id=doc_id,
                    document_version_id=version_id,
                    stage=RagStage.PARSE.value,
                    payload_ref=blob_key,
                )
            )
            session.add(OutboxEventRow(id=event_id, topic="rag.parse", payload_json=json.dumps(payload)))
            await session.commit()
        return {"doc_id": doc_id, "version_id": version_id, "job_id": run_id}

    async def claim_stage(self, run_id: str) -> dict[str, Any] | None:
        now = _utcnow()
        async with self.db.session() as session:
            row = await session.get(IngestStageRunRow, run_id, with_for_update=True)
            if row is None or row.status == "succeeded" or row.status == "failed":
                return None
            if row.status == "processing" and row.lease_until and row.lease_until > now:
                return None
            row.status = "processing"
            row.attempt += 1
            row.lease_until = now + timedelta(seconds=settings.rag_stage_lease_seconds)
            row.updated_at = now
            await session.commit()
            return {
                "id": row.id,
                "attempt": row.attempt,
                "document_id": row.document_id,
                "document_version_id": row.document_version_id,
                "stage": row.stage,
            }

    async def complete_stage(
        self, *, run_id: str, payload_ref: str, content_hash: str | None, chunk_count: int | None = None
    ) -> None:
        now = _utcnow()
        async with self.db.session() as session:
            run = await session.get(IngestStageRunRow, run_id, with_for_update=True)
            if run is None or run.status != "processing":
                raise RuntimeError("stage is not claimed")
            run.status = "succeeded"
            run.payload_ref = payload_ref
            run.content_hash = content_hash
            run.lease_until = None
            run.updated_at = now
            stage = RagStage(run.stage)
            doc = await session.get(DocumentRow, run.document_id, with_for_update=True)
            version = await session.get(DocumentVersionRow, run.document_version_id, with_for_update=True)
            assert doc is not None and version is not None
            if stage == RagStage.INDEX:
                old_versions = await session.execute(
                    select(DocumentVersionRow).where(
                        DocumentVersionRow.document_id == run.document_id,
                        DocumentVersionRow.id != version.id,
                        DocumentVersionRow.active.is_(True),
                    )
                )
                for old_version in old_versions.scalars():
                    old_version.active = False
                version.status = DocumentStatus.READY.value
                version.active = True
                doc.status = DocumentStatus.READY.value
                if chunk_count is not None:
                    doc.chunk_count = chunk_count
            else:
                next_stage = {RagStage.PARSE: RagStage.CHUNK, RagStage.CHUNK: RagStage.EMBED, RagStage.EMBED: RagStage.INDEX}[stage]
                next_run_id, event_id = str(uuid4()), str(uuid4())
                session.add(
                    IngestStageRunRow(
                        id=next_run_id,
                        document_id=run.document_id,
                        document_version_id=run.document_version_id,
                        stage=next_stage.value,
                        payload_ref=payload_ref,
                        content_hash=content_hash,
                    )
                )
                event = {
                    "job_id": next_run_id,
                    "doc_id": run.document_id,
                    "document_version_id": run.document_version_id,
                    "stage": next_stage.value,
                    "payload_ref": payload_ref,
                    "attempt": 0,
                    "tenant_id": version.tenant_id,
                    "kb_id": version.kb_id,
                    "content_hash": content_hash,
                    "outbox_event_id": event_id,
                }
                session.add(OutboxEventRow(id=event_id, topic=f"rag.{next_stage.value}", payload_json=json.dumps(event)))
                doc.status = {
                    RagStage.PARSE: DocumentStatus.CHUNKING.value,
                    RagStage.CHUNK: DocumentStatus.EMBEDDING.value,
                    RagStage.EMBED: DocumentStatus.INDEXING.value,
                }[stage]
            if content_hash:
                version.content_hash = content_hash
                doc.content_hash = content_hash
            await session.commit()

    async def save_parent_chunks(self, document_version_id: str, chunks: list[dict[str, Any]]) -> None:
        """父块不进入向量索引，仅以 version 范围持久化供 child 命中回填。"""
        async with self.db.session() as session:
            for chunk in chunks:
                if (chunk.get("metadata") or {}).get("kind") != "parent":
                    continue
                parent_id = str(chunk["chunk_id"])
                existing = await session.get(ParentChunkRow, parent_id)
                if existing is None:
                    session.add(
                        ParentChunkRow(
                            id=parent_id,
                            document_version_id=document_version_id,
                            text=str(chunk.get("text") or ""),
                            source=str((chunk.get("metadata") or {}).get("source") or ""),
                        )
                    )
            await session.commit()

    async def hydrate_parent_hits(self, hits: list[Any], *, active_version_ids: set[str]) -> list[Any]:
        parent_ids = {str(hit.parent_id) for hit in hits if getattr(hit, "parent_id", None)}
        if not parent_ids:
            return hits
        async with self.db.session() as session:
            rows = await session.execute(select(ParentChunkRow).where(ParentChunkRow.id.in_(parent_ids)))
            parents = {row.id: row for row in rows.scalars() if row.document_version_id in active_version_ids}
        hydrated, seen = [], set()
        for hit in hits:
            parent = parents.get(str(getattr(hit, "parent_id", "")))
            key = parent.id if parent else hit.chunk_id
            if key in seen:
                continue
            seen.add(key)
            if parent:
                hydrated.append(hit.model_copy(update={"text": parent.text, "source": parent.source}))
            else:
                hydrated.append(hit)
        return hydrated

    async def fail_stage(self, *, run_id: str, error: str, retryable: bool) -> None:
        async with self.db.session() as session:
            run = await session.get(IngestStageRunRow, run_id, with_for_update=True)
            if run is None:
                return
            terminal = not retryable or run.attempt >= settings.rag_max_retries
            run.status = "failed" if terminal else "queued"
            run.lease_until = None
            run.error_message = error[:4000]
            run.updated_at = _utcnow()
            if terminal:
                doc = await session.get(DocumentRow, run.document_id, with_for_update=True)
                version = await session.get(DocumentVersionRow, run.document_version_id, with_for_update=True)
                if doc:
                    doc.status, doc.error_message = DocumentStatus.FAILED.value, run.error_message
                if version:
                    version.status = DocumentStatus.FAILED.value
            else:
                event_id = str(uuid4())
                payload = {
                    "job_id": run.id,
                    "doc_id": run.document_id,
                    "document_version_id": run.document_version_id,
                    "stage": run.stage,
                    "payload_ref": run.payload_ref,
                    "attempt": run.attempt,
                    "content_hash": run.content_hash,
                    "outbox_event_id": event_id,
                }
                session.add(OutboxEventRow(id=event_id, topic=f"rag.{run.stage}", payload_json=json.dumps(payload)))
            await session.commit()

    async def get_stage_run(self, principal: Principal, run_id: str) -> dict[str, Any] | None:
        async with self.db.session() as session:
            row = await session.get(IngestStageRunRow, run_id)
            if row is None:
                return None
            version = await session.get(DocumentVersionRow, row.document_version_id)
            if version is None or version.tenant_id != principal.tenant_id:
                return None
            if not principal.is_admin and version.kb_id not in principal.kb_ids:
                return None
            return {
                "id": row.id,
                "doc_id": row.document_id,
                "document_version_id": row.document_version_id,
                "stage": row.stage,
                "status": row.status,
                "attempt": row.attempt,
                "error_message": row.error_message,
                "updated_at": row.updated_at.isoformat(),
            }

    async def replay_failed_stage(self, principal: Principal, run_id: str) -> None:
        if not principal.is_admin:
            raise PermissionError("stage replay requires admin")
        async with self.db.session() as session:
            run = await session.get(IngestStageRunRow, run_id, with_for_update=True)
            if run is None:
                raise LookupError("stage run not found")
            version = await session.get(DocumentVersionRow, run.document_version_id)
            if version is None or version.tenant_id != principal.tenant_id:
                raise PermissionError("stage run access denied")
            if run.status != "failed":
                raise ValueError("only failed stage runs may be replayed")
            event_id = str(uuid4())
            run.status, run.error_message, run.lease_until = "queued", None, None
            session.add(
                OutboxEventRow(
                    id=event_id,
                    topic=f"rag.{run.stage}",
                    payload_json=json.dumps(
                        {
                            "job_id": run.id,
                            "doc_id": run.document_id,
                            "document_version_id": run.document_version_id,
                            "stage": run.stage,
                            "payload_ref": run.payload_ref,
                            "content_hash": run.content_hash,
                            "tenant_id": version.tenant_id,
                            "kb_id": version.kb_id,
                            "outbox_event_id": event_id,
                        }
                    ),
                )
            )
            await session.commit()

    async def rag_metrics(self) -> dict[str, int]:
        async with self.db.session() as session:
            pending = (await session.execute(select(OutboxEventRow).where(OutboxEventRow.status == "pending"))).scalars().all()
            failed = (await session.execute(select(IngestStageRunRow).where(IngestStageRunRow.status == "failed"))).scalars().all()
            leased = (await session.execute(select(IngestStageRunRow).where(IngestStageRunRow.status == "processing"))).scalars().all()
            return {"outbox_pending": len(pending), "stage_failed": len(failed), "stage_processing": len(leased)}

    async def pending_outbox(self, limit: int | None = None) -> list[dict[str, Any]]:
        async with self.db.session() as session:
            rows = (
                await session.execute(
                    select(OutboxEventRow)
                    .where(OutboxEventRow.status == "pending", OutboxEventRow.available_at <= _utcnow())
                    .order_by(OutboxEventRow.created_at)
                    .limit(limit or settings.rag_outbox_batch_size)
                )
            ).scalars().all()
            return [{"id": row.id, "topic": row.topic, "payload": json.loads(row.payload_json), "attempt": row.attempt} for row in rows]

    async def mark_outbox_published(self, event_id: str) -> None:
        async with self.db.session() as session:
            row = await session.get(OutboxEventRow, event_id, with_for_update=True)
            if row:
                row.status, row.published_at, row.error_message = "published", _utcnow(), None
                await session.commit()

    async def mark_outbox_failed(self, event_id: str, error: str) -> None:
        async with self.db.session() as session:
            row = await session.get(OutboxEventRow, event_id, with_for_update=True)
            if row:
                row.attempt += 1
                row.error_message = error[:4000]
                await session.commit()
