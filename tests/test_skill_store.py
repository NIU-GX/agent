"""SkillStore CRUD + SkillRegistry.reload_from。"""

from __future__ import annotations

import pytest

from agent_core.skills import SkillRegistry, filesystem_skill_seeds
from shared.skill_store import SkillStore


@pytest.mark.asyncio
async def test_skill_store_seed_and_crud():
    pytest.importorskip("aiosqlite")
    from shared.db import Database

    db = Database(dsn="sqlite+aiosqlite:///:memory:")
    await db.create_tables()
    store = SkillStore(db)

    await store.ensure_defaults(
        [
            {
                "name": "demo",
                "description": "d",
                "body": "body1",
                "tools": ["calculator"],
                "mcp": [],
            }
        ]
    )
    await store.ensure_defaults(
        [{"name": "demo", "description": "ignore", "body": "should-not-overwrite"}]
    )
    got = await store.get("demo")
    assert got is not None
    assert got["body"] == "body1"

    created = await store.create(name="other", description="x", body="y", tools=["http_get"])
    assert created["name"] == "other"

    await store.set_enabled("demo", False)
    enabled = await store.list_skills(enabled_only=True)
    assert all(s["name"] != "demo" for s in enabled)

    updated = await store.update("other", body="yy", tools=["calculator", "http_get"])
    assert updated["body"] == "yy"
    assert updated["tools"] == ["calculator", "http_get"]

    await store.delete("other")
    assert await store.get("other") is None
    await db.aclose()


def test_skill_registry_reload_from_and_filesystem_seeds():
    reg = SkillRegistry(load_filesystem=False)
    assert reg.catalog() == []
    reg.reload_from(
        [
            {
                "name": "kb",
                "description": "qa",
                "body": "use retrieve",
                "tools": ["retrieve"],
                "mcp": [],
                "enabled": True,
            },
            {
                "name": "off",
                "description": "x",
                "body": "y",
                "tools": [],
                "mcp": [],
                "enabled": False,
            },
        ]
    )
    names = {s["name"] for s in reg.catalog()}
    assert names == {"kb"}
    act = reg.activate("kb")
    assert act["ok"] is True
    assert "retrieve" in act["unlocked_tools"]

    seeds = filesystem_skill_seeds("skills")
    # 仓库内至少有示例 skill；若目录缺失则为空
    assert isinstance(seeds, list)
