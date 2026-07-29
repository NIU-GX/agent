"""McpStore CRUD + JSON 种子。"""

from __future__ import annotations

import pytest

from shared.mcp_store import McpStore


@pytest.mark.asyncio
async def test_mcp_store_defaults_and_crud():
    pytest.importorskip("aiosqlite")
    from shared.db import Database

    db = Database(dsn="sqlite+aiosqlite:///:memory:")
    await db.create_tables()
    store = McpStore(db)

    seeds = McpStore.parse_servers_json(
        '[{"name":"fs","command":"npx","args":["-y","@modelcontextprotocol/server-filesystem","/tmp"]}]'
    )
    await store.ensure_defaults(seeds)
    await store.ensure_defaults(seeds)  # no overwrite
    items = await store.list_servers()
    assert len(items) == 1
    assert items[0]["name"] == "fs"
    assert items[0]["command"] == "npx"

    created = await store.create(name="custom", command="echo", args=["hi"], env={"A": "1"})
    assert created["enabled"] is True

    await store.set_enabled("custom", False)
    enabled = await store.list_servers(enabled_only=True)
    assert all(s["name"] != "custom" for s in enabled)

    await store.set_last_error("fs", "boom")
    fs = await store.get("fs")
    assert fs is not None
    assert fs["last_error"] == "boom"
    await store.set_last_error("fs", None)

    updated = await store.update("fs", command="npx", args=["-y", "pkg"])
    assert updated["args"] == ["-y", "pkg"]

    await store.delete("custom")
    assert await store.get("custom") is None
    await db.aclose()


def test_parse_servers_json_invalid():
    assert McpStore.parse_servers_json("not-json") == []
    assert McpStore.parse_servers_json("{}") == []
