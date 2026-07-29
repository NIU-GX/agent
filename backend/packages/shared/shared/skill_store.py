"""Skill 持久化（独立能力）：CRUD、启用开关、种子导入；不依赖 agent-core。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import Boolean, DateTime, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import Base, Database
from shared.logging import get_logger

logger = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CapabilitySkillRow(Base):
    __tablename__ = "capability_skills"

    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tools_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    mcp_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


def _parse_str_list(raw: str) -> List[str]:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [str(x).strip() for x in data if str(x).strip()]


def _row_to_dict(row: CapabilitySkillRow) -> Dict[str, Any]:
    return {
        "name": row.name,
        "description": row.description,
        "body": row.body,
        "tools": _parse_str_list(row.tools_json),
        "mcp": _parse_str_list(row.mcp_json),
        "enabled": bool(row.enabled),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


class SkillStore:
    """Skill CRUD；ensure_defaults 从外部种子导入（已存在不覆盖）。"""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def ensure_defaults(self, seeds: Sequence[dict[str, Any]] | None = None) -> None:
        for item in seeds or ():
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            existing = await self.get(name)
            if existing is not None:
                continue
            await self.create(
                name=name,
                description=str(item.get("description") or ""),
                body=str(item.get("body") or ""),
                tools=list(item.get("tools") or []),
                mcp=list(item.get("mcp") or []),
                enabled=bool(item.get("enabled", True)),
            )
            logger.info("seeded skill name=%s", name)

    async def list_skills(self, *, enabled_only: bool = False) -> List[Dict[str, Any]]:
        async with self.db.session() as session:
            stmt = select(CapabilitySkillRow).order_by(CapabilitySkillRow.name.asc())
            if enabled_only:
                stmt = stmt.where(CapabilitySkillRow.enabled.is_(True))
            rows = (await session.execute(stmt)).scalars().all()
            return [_row_to_dict(r) for r in rows]

    async def get(self, name: str) -> Optional[Dict[str, Any]]:
        async with self.db.session() as session:
            row = await session.get(CapabilitySkillRow, name)
            return _row_to_dict(row) if row else None

    async def create(
        self,
        *,
        name: str,
        description: str = "",
        body: str = "",
        tools: list[str] | None = None,
        mcp: list[str] | None = None,
        enabled: bool = True,
    ) -> Dict[str, Any]:
        name = name.strip()
        if not name:
            raise ValueError("name must not be empty")
        now = _utcnow()
        async with self.db.session() as session:
            existing = await session.get(CapabilitySkillRow, name)
            if existing is not None:
                raise ValueError(f"skill already exists: {name}")
            row = CapabilitySkillRow(
                name=name,
                description=description or "",
                body=body or "",
                tools_json=json.dumps(list(tools or [])),
                mcp_json=json.dumps(list(mcp or [])),
                enabled=enabled,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _row_to_dict(row)

    async def update(self, name: str, **fields: Any) -> Dict[str, Any]:
        async with self.db.session() as session:
            row = await session.get(CapabilitySkillRow, name)
            if row is None:
                raise KeyError(f"skill not found: {name}")
            if "description" in fields and fields["description"] is not None:
                row.description = str(fields["description"])
            if "body" in fields and fields["body"] is not None:
                row.body = str(fields["body"])
            if "tools" in fields and fields["tools"] is not None:
                row.tools_json = json.dumps(list(fields["tools"]))
            if "mcp" in fields and fields["mcp"] is not None:
                row.mcp_json = json.dumps(list(fields["mcp"]))
            if "enabled" in fields and fields["enabled"] is not None:
                row.enabled = bool(fields["enabled"])
            row.updated_at = _utcnow()
            await session.commit()
            await session.refresh(row)
            return _row_to_dict(row)

    async def delete(self, name: str) -> None:
        async with self.db.session() as session:
            row = await session.get(CapabilitySkillRow, name)
            if row is None:
                raise KeyError(f"skill not found: {name}")
            await session.delete(row)
            await session.commit()
        logger.info("deleted skill name=%s", name)

    async def set_enabled(self, name: str, enabled: bool) -> Dict[str, Any]:
        return await self.update(name, enabled=enabled)
