"""提示词版本管理（独立能力）：多版本存储、激活指针、回退与内存缓存。

不依赖 agent-core；种子内容由装配层传入。可单独用于任意 key 的版本管理。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence
from uuid import uuid4

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import Base, Database
from shared.logging import get_logger

logger = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PromptRow(Base):
    """提示词逻辑实体：key 唯一，active_version 指向当前生效版本号。"""

    __tablename__ = "prompts"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    active_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class PromptVersionRow(Base):
    """不可变版本快照；回退只改 PromptRow.active_version，不删历史。"""

    __tablename__ = "prompt_versions"
    __table_args__ = (UniqueConstraint("prompt_key", "version", name="uq_prompt_version"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid4()))
    prompt_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    change_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class PromptStore:
    """提示词 CRUD / 发版 / 回退；维护 active content 内存缓存。"""

    def __init__(self, db: Database) -> None:
        self.db = db
        self._cache: Dict[str, str] = {}

    async def ensure_defaults(self, seeds: Sequence[dict[str, str]] | None = None) -> None:
        """按外部种子注册缺失的 key；已存在不覆盖。seeds 为空则只刷新缓存。"""
        for item in seeds or ():
            key = item.get("key") or ""
            content = item.get("content") or ""
            if not key or not content:
                continue
            existing = await self.get_prompt(key)
            if existing is None:
                await self._create_prompt(
                    key=key,
                    name=item.get("name") or key,
                    description=item.get("description") or "",
                    content=content,
                    change_note="seed default",
                    created_by="system",
                )
                logger.info("seeded prompt key=%s v1", key)
        await self.refresh_cache()

    async def refresh_cache(self) -> None:
        async with self.db.session() as session:
            prompts = (await session.execute(select(PromptRow))).scalars().all()
            cache: Dict[str, str] = {}
            for p in prompts:
                ver = await session.execute(
                    select(PromptVersionRow).where(
                        PromptVersionRow.prompt_key == p.key,
                        PromptVersionRow.version == p.active_version,
                    )
                )
                row = ver.scalar_one_or_none()
                if row:
                    cache[p.key] = row.content
            self._cache = cache

    def lookup(self, key: str) -> Optional[str]:
        """若缓存中有激活正文则返回，否则 None（由调用方决定回退策略）。"""
        return self._cache.get(key)

    def get_active(self, key: str, default: str | None = None) -> str:
        """同步读取当前激活正文。"""
        if key in self._cache:
            return self._cache[key]
        return default if default is not None else ""

    async def list_prompts(self) -> List[Dict[str, Any]]:
        async with self.db.session() as session:
            rows = (
                await session.execute(select(PromptRow).order_by(PromptRow.key.asc()))
            ).scalars().all()
            out: List[Dict[str, Any]] = []
            for p in rows:
                ver = await session.execute(
                    select(PromptVersionRow).where(
                        PromptVersionRow.prompt_key == p.key,
                        PromptVersionRow.version == p.active_version,
                    )
                )
                active = ver.scalar_one_or_none()
                count = (
                    await session.execute(
                        select(PromptVersionRow).where(PromptVersionRow.prompt_key == p.key)
                    )
                ).scalars().all()
                out.append(
                    {
                        "key": p.key,
                        "name": p.name,
                        "description": p.description,
                        "active_version": p.active_version,
                        "version_count": len(count),
                        "content_preview": (active.content[:160] + "…")
                        if active and len(active.content) > 160
                        else (active.content if active else ""),
                        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
                    }
                )
            return out

    async def get_prompt(self, key: str) -> Optional[Dict[str, Any]]:
        async with self.db.session() as session:
            p = await session.get(PromptRow, key)
            if p is None:
                return None
            versions = (
                await session.execute(
                    select(PromptVersionRow)
                    .where(PromptVersionRow.prompt_key == key)
                    .order_by(PromptVersionRow.version.desc())
                )
            ).scalars().all()
            return {
                "key": p.key,
                "name": p.name,
                "description": p.description,
                "active_version": p.active_version,
                "updated_at": p.updated_at.isoformat() if p.updated_at else None,
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "versions": [_version_to_dict(v, p.active_version) for v in versions],
            }

    async def create_version(
        self,
        key: str,
        content: str,
        *,
        change_note: str | None = None,
        created_by: str | None = None,
        activate: bool = True,
    ) -> Dict[str, Any]:
        content = content.strip()
        if not content:
            raise ValueError("content must not be empty")
        async with self.db.session() as session:
            p = await session.get(PromptRow, key)
            if p is None:
                raise KeyError(f"prompt not found: {key}")
            latest = (
                await session.execute(
                    select(PromptVersionRow)
                    .where(PromptVersionRow.prompt_key == key)
                    .order_by(PromptVersionRow.version.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            next_ver = (latest.version + 1) if latest else 1
            row = PromptVersionRow(
                id=str(uuid4()),
                prompt_key=key,
                version=next_ver,
                content=content,
                change_note=change_note,
                created_by=created_by,
                created_at=_utcnow(),
            )
            session.add(row)
            if activate:
                p.active_version = next_ver
                p.updated_at = _utcnow()
            await session.commit()
            result = _version_to_dict(row, p.active_version)
        await self.refresh_cache()
        return result

    async def rollback(self, key: str, version: int) -> Dict[str, Any]:
        """回退到历史版本：仅切换 active_version，保留全部版本历史。"""
        async with self.db.session() as session:
            p = await session.get(PromptRow, key)
            if p is None:
                raise KeyError(f"prompt not found: {key}")
            ver = (
                await session.execute(
                    select(PromptVersionRow).where(
                        PromptVersionRow.prompt_key == key,
                        PromptVersionRow.version == version,
                    )
                )
            ).scalar_one_or_none()
            if ver is None:
                raise KeyError(f"version not found: {key}@v{version}")
            prev = p.active_version
            p.active_version = version
            p.updated_at = _utcnow()
            await session.commit()
            detail = {
                "key": key,
                "from_version": prev,
                "active_version": version,
                "content": ver.content,
                "change_note": ver.change_note,
            }
        await self.refresh_cache()
        logger.info("prompt rollback key=%s %s -> %s", key, prev, version)
        return detail

    async def _create_prompt(
        self,
        *,
        key: str,
        name: str,
        description: str,
        content: str,
        change_note: str | None = None,
        created_by: str | None = None,
    ) -> None:
        now = _utcnow()
        async with self.db.session() as session:
            session.add(
                PromptRow(
                    key=key,
                    name=name,
                    description=description,
                    active_version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                PromptVersionRow(
                    id=str(uuid4()),
                    prompt_key=key,
                    version=1,
                    content=content,
                    change_note=change_note,
                    created_by=created_by,
                    created_at=now,
                )
            )
            await session.commit()


def _version_to_dict(row: PromptVersionRow, active_version: int) -> Dict[str, Any]:
    return {
        "id": row.id,
        "prompt_key": row.prompt_key,
        "version": row.version,
        "content": row.content,
        "change_note": row.change_note,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "is_active": row.version == active_version,
    }
