"""Webhook 工具持久化（独立能力）：CRUD、启用开关；不依赖 agent-core。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import Boolean, DateTime, Float, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import Base, Database
from shared.logging import get_logger

logger = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CapabilityToolRow(Base):
    """动态 Webhook 工具；内置工具不入库。"""

    __tablename__ = "capability_tools"

    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    parameters_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    tier: Mapped[str] = mapped_column(String(32), nullable=False, default="optional")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    webhook_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    webhook_method: Mapped[str] = mapped_column(String(16), nullable=False, default="POST")
    webhook_headers_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    timeout_sec: Mapped[float] = mapped_column(Float, nullable=False, default=30.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class CapabilityToolFlagRow(Base):
    """任意工具名的启用覆盖（主要用于内置 / 元工具）。"""

    __tablename__ = "capability_tool_flags"

    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

def _row_to_dict(row: CapabilityToolRow) -> Dict[str, Any]:
    try:
        parameters = json.loads(row.parameters_json or "{}")
    except json.JSONDecodeError:
        parameters = {}
    try:
        headers = json.loads(row.webhook_headers_json or "{}")
    except json.JSONDecodeError:
        headers = {}
    return {
        "name": row.name,
        "description": row.description,
        "parameters": parameters if isinstance(parameters, dict) else {},
        "tier": row.tier,
        "enabled": bool(row.enabled),
        "webhook_url": row.webhook_url,
        "webhook_method": (row.webhook_method or "POST").upper(),
        "webhook_headers": headers if isinstance(headers, dict) else {},
        "timeout_sec": float(row.timeout_sec or 30.0),
        "source": "webhook",
        "mutable": True,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


class ToolStore:
    """Webhook 工具 CRUD；list_enabled_webhooks 供运行时注入。"""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def list_tools(self) -> List[Dict[str, Any]]:
        async with self.db.session() as session:
            rows = (
                await session.execute(
                    select(CapabilityToolRow).order_by(CapabilityToolRow.name.asc())
                )
            ).scalars().all()
            return [_row_to_dict(r) for r in rows]

    async def list_enabled_webhooks(self) -> List[Dict[str, Any]]:
        async with self.db.session() as session:
            rows = (
                await session.execute(
                    select(CapabilityToolRow)
                    .where(CapabilityToolRow.enabled.is_(True))
                    .order_by(CapabilityToolRow.name.asc())
                )
            ).scalars().all()
            return [_row_to_dict(r) for r in rows]

    async def get(self, name: str) -> Optional[Dict[str, Any]]:
        async with self.db.session() as session:
            row = await session.get(CapabilityToolRow, name)
            return _row_to_dict(row) if row else None

    async def create(
        self,
        *,
        name: str,
        description: str,
        parameters: dict[str, Any] | None = None,
        webhook_url: str,
        webhook_method: str = "POST",
        webhook_headers: dict[str, Any] | None = None,
        timeout_sec: float = 30.0,
        tier: str = "optional",
        enabled: bool = True,
    ) -> Dict[str, Any]:
        name = name.strip()
        webhook_url = webhook_url.strip()
        if not name:
            raise ValueError("name must not be empty")
        if not webhook_url:
            raise ValueError("webhook_url must not be empty")
        if tier not in {"optional", "core", "meta"}:
            tier = "optional"
        now = _utcnow()
        async with self.db.session() as session:
            existing = await session.get(CapabilityToolRow, name)
            if existing is not None:
                raise ValueError(f"tool already exists: {name}")
            row = CapabilityToolRow(
                name=name,
                description=description or "",
                parameters_json=json.dumps(parameters or {"type": "object", "properties": {}}),
                tier=tier,
                enabled=enabled,
                webhook_url=webhook_url,
                webhook_method=(webhook_method or "POST").upper(),
                webhook_headers_json=json.dumps(webhook_headers or {}),
                timeout_sec=float(timeout_sec or 30.0),
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            result = _row_to_dict(row)
        logger.info("created webhook tool name=%s", name)
        return result

    async def update(self, name: str, **fields: Any) -> Dict[str, Any]:
        async with self.db.session() as session:
            row = await session.get(CapabilityToolRow, name)
            if row is None:
                raise KeyError(f"tool not found: {name}")
            if "description" in fields and fields["description"] is not None:
                row.description = str(fields["description"])
            if "parameters" in fields and fields["parameters"] is not None:
                row.parameters_json = json.dumps(fields["parameters"])
            if "webhook_url" in fields and fields["webhook_url"] is not None:
                url = str(fields["webhook_url"]).strip()
                if not url:
                    raise ValueError("webhook_url must not be empty")
                row.webhook_url = url
            if "webhook_method" in fields and fields["webhook_method"] is not None:
                row.webhook_method = str(fields["webhook_method"]).upper()
            if "webhook_headers" in fields and fields["webhook_headers"] is not None:
                row.webhook_headers_json = json.dumps(fields["webhook_headers"])
            if "timeout_sec" in fields and fields["timeout_sec"] is not None:
                row.timeout_sec = float(fields["timeout_sec"])
            if "tier" in fields and fields["tier"] is not None:
                tier = str(fields["tier"])
                if tier in {"optional", "core", "meta"}:
                    row.tier = tier
            if "enabled" in fields and fields["enabled"] is not None:
                row.enabled = bool(fields["enabled"])
            row.updated_at = _utcnow()
            await session.commit()
            await session.refresh(row)
            return _row_to_dict(row)

    async def delete(self, name: str) -> None:
        async with self.db.session() as session:
            row = await session.get(CapabilityToolRow, name)
            if row is None:
                raise KeyError(f"tool not found: {name}")
            await session.delete(row)
            await session.commit()
        logger.info("deleted webhook tool name=%s", name)

    async def set_enabled(self, name: str, enabled: bool) -> Dict[str, Any]:
        return await self.update(name, enabled=enabled)

    async def list_flags(self) -> Dict[str, bool]:
        async with self.db.session() as session:
            rows = (await session.execute(select(CapabilityToolFlagRow))).scalars().all()
            return {r.name: bool(r.enabled) for r in rows}

    async def set_flag(self, name: str, enabled: bool) -> Dict[str, Any]:
        name = name.strip()
        if not name:
            raise ValueError("name must not be empty")
        async with self.db.session() as session:
            row = await session.get(CapabilityToolFlagRow, name)
            if row is None:
                row = CapabilityToolFlagRow(name=name, enabled=enabled, updated_at=_utcnow())
                session.add(row)
            else:
                row.enabled = enabled
                row.updated_at = _utcnow()
            await session.commit()
            return {"name": name, "enabled": enabled}