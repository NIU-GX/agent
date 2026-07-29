"""Webhook ToolStore CRUD + ToolRegistry webhook 注入。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agent_core.tools import ToolRegistry
from shared.tool_store import ToolStore


@pytest.mark.asyncio
async def test_tool_store_crud_and_enabled():
    pytest.importorskip("aiosqlite")
    from shared.db import Database

    db = Database(dsn="sqlite+aiosqlite:///:memory:")
    await db.create_tables()
    store = ToolStore(db)

    created = await store.create(
        name="echo_hook",
        description="echo",
        parameters={"type": "object", "properties": {"q": {"type": "string"}}},
        webhook_url="https://example.com/hook",
        webhook_headers={"X-Token": "t"},
        timeout_sec=12,
    )
    assert created["name"] == "echo_hook"
    assert created["enabled"] is True
    assert created["webhook_headers"]["X-Token"] == "t"

    items = await store.list_tools()
    assert len(items) == 1
    enabled = await store.list_enabled_webhooks()
    assert len(enabled) == 1

    await store.set_enabled("echo_hook", False)
    assert await store.list_enabled_webhooks() == []

    updated = await store.update("echo_hook", description="updated", enabled=True)
    assert updated["description"] == "updated"
    assert updated["enabled"] is True

    await store.set_flag("retrieve", False)
    flags = await store.list_flags()
    assert flags["retrieve"] is False

    await store.delete("echo_hook")
    assert await store.get("echo_hook") is None
    await db.aclose()


@pytest.mark.asyncio
async def test_registry_sync_webhook_and_catalog():
    tools = ToolRegistry()
    tools.sync_webhooks(
        [
            {
                "name": "weather",
                "description": "get weather",
                "parameters": {"type": "object", "properties": {}},
                "webhook_url": "https://example.com/w",
                "webhook_method": "POST",
                "webhook_headers": {},
                "timeout_sec": 5,
                "tier": "optional",
                "enabled": True,
            }
        ]
    )
    names = {i["name"] for i in tools.catalog()}
    assert "weather" in names
    assert "retrieve" in names

    tools.set_enabled("weather", False)
    names2 = {i["name"] for i in tools.catalog()}
    assert "weather" not in names2
    admin = {i["name"]: i for i in tools.admin_catalog()}
    assert admin["weather"]["enabled"] is False

    with patch("httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client_cls.return_value.__aenter__.return_value = client
        resp = AsyncMock()
        resp.is_error = False
        resp.status_code = 200
        resp.text = '{"ok":true}'
        resp.json = lambda: {"ok": True}
        client.request = AsyncMock(return_value=resp)
        tools.set_enabled("weather", True)
        result = await tools.call("weather", {"city": "SZ"})
        assert result["ok"] is True
        client.request.assert_awaited()
