"""MCP Server 配置持久化（独立能力）：CRUD、启用开关、JSON 种子；不依赖 agent-core。"""

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


class CapabilityMcpServerRow(Base):
    __tablename__ = "capability_mcp_servers"

    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    command: Mapped[str] = mapped_column(String(512), nullable=False)
    args_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    env_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


def _parse_list(raw: str) -> List[Any]:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _parse_dict(raw: str) -> Dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _row_to_dict(row: CapabilityMcpServerRow) -> Dict[str, Any]:
    return {
        "name": row.name,
        "command": row.command,
        "args": _parse_list(row.args_json),
        "env": _parse_dict(row.env_json),
        "enabled": bool(row.enabled),
        "last_error": row.last_error,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


class McpStore:
    """MCP Server 配置 CRUD；ensure_defaults 从 MCP_SERVERS_JSON 种子导入。"""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def ensure_defaults(self, seeds: Sequence[dict[str, Any]] | None = None) -> None:
        for item in seeds or ():
            name = str(item.get("name") or "").strip()
            command = str(item.get("command") or "").strip()
            if not name or not command:
                continue
            existing = await self.get(name)
            if existing is not None:
                continue
            await self.create(
                name=name,
                command=command,
                args=list(item.get("args") or []),
                env=dict(item.get("env") or {}),
                enabled=bool(item.get("enabled", True)),
            )
            logger.info("seeded mcp server name=%s", name)

    @staticmethod
    def parse_servers_json(servers_json: str) -> List[Dict[str, Any]]:
        try:
            data = json.loads(servers_json or "[]")
        except json.JSONDecodeError:
            logger.error("invalid mcp_servers_json for seed")
            return []
        if not isinstance(data, list):
            return []
        out: List[Dict[str, Any]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            out.append(item)
        return out

    async def list_servers(self, *, enabled_only: bool = False) -> List[Dict[str, Any]]:
        async with self.db.session() as session:
            stmt = select(CapabilityMcpServerRow).order_by(CapabilityMcpServerRow.name.asc())
            if enabled_only:
                stmt = stmt.where(CapabilityMcpServerRow.enabled.is_(True))
            rows = (await session.execute(stmt)).scalars().all()
            return [_row_to_dict(r) for r in rows]

    async def get(self, name: str) -> Optional[Dict[str, Any]]:
        async with self.db.session() as session:
            row = await session.get(CapabilityMcpServerRow, name)
            return _row_to_dict(row) if row else None

    async def create(
        self,
        *,
        name: str,
        command: str,
        args: list[Any] | None = None,
        env: dict[str, Any] | None = None,
        enabled: bool = True,
    ) -> Dict[str, Any]:
        name = name.strip()
        command = command.strip()
        if not name:
            raise ValueError("name must not be empty")
        if not command:
            raise ValueError("command must not be empty")
        now = _utcnow()
        async with self.db.session() as session:
            existing = await session.get(CapabilityMcpServerRow, name)
            if existing is not None:
                raise ValueError(f"mcp server already exists: {name}")
            row = CapabilityMcpServerRow(
                name=name,
                command=command,
                args_json=json.dumps(list(args or [])),
                env_json=json.dumps(dict(env or {})),
                enabled=enabled,
                last_error=None,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _row_to_dict(row)

    async def update(self, name: str, **fields: Any) -> Dict[str, Any]:
        async with self.db.session() as session:
            row = await session.get(CapabilityMcpServerRow, name)
            if row is None:
                raise KeyError(f"mcp server not found: {name}")
            if "command" in fields and fields["command"] is not None:
                command = str(fields["command"]).strip()
                if not command:
                    raise ValueError("command must not be empty")
                row.command = command
            if "args" in fields and fields["args"] is not None:
                row.args_json = json.dumps(list(fields["args"]))
            if "env" in fields and fields["env"] is not None:
                row.env_json = json.dumps(dict(fields["env"]))
            if "enabled" in fields and fields["enabled"] is not None:
                row.enabled = bool(fields["enabled"])
            if "last_error" in fields:
                row.last_error = fields["last_error"]
            row.updated_at = _utcnow()
            await session.commit()
            await session.refresh(row)
            return _row_to_dict(row)

    async def set_last_error(self, name: str, error: str | None) -> None:
        async with self.db.session() as session:
            row = await session.get(CapabilityMcpServerRow, name)
            if row is None:
                return
            row.last_error = error
            row.updated_at = _utcnow()
            await session.commit()

    async def delete(self, name: str) -> None:
        async with self.db.session() as session:
            row = await session.get(CapabilityMcpServerRow, name)
            if row is None:
                raise KeyError(f"mcp server not found: {name}")
            await session.delete(row)
            await session.commit()
        logger.info("deleted mcp server name=%s", name)

    async def set_enabled(self, name: str, enabled: bool) -> Dict[str, Any]:
        return await self.update(name, enabled=enabled)
