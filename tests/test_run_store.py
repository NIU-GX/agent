"""Agent run 指针落库：只存 session/run/trace，不存轨迹正文。"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_agent_run_pointer_upsert_and_list():
    pytest.importorskip("aiosqlite")
    from shared.db import Database
    from shared.run_store import AgentRunStore

    db = Database(dsn="sqlite+aiosqlite:///:memory:")
    await db.create_tables()
    store = AgentRunStore(db)

    row = await store.upsert(
        run_id="run-1",
        session_id="sess-a",
        trace_id="trace-1",
        langfuse_url="http://localhost:3000/trace/trace-1",
        strategy="react",
        status="started",
        tenant_id="t1",
    )
    assert row["run_id"] == "run-1"
    assert row["status"] == "started"

    updated = await store.upsert(
        run_id="run-1",
        session_id="sess-a",
        trace_id="trace-1b",
        langfuse_url="http://localhost:3000/trace/trace-1b",
        status="completed",
    )
    assert updated["trace_id"] == "trace-1b"
    assert updated["status"] == "completed"
    assert updated["strategy"] == "react"

    await store.upsert(
        run_id="run-2",
        session_id="sess-a",
        trace_id="trace-2",
        status="error",
        tenant_id="t1",
    )
    items = await store.list_by_session("sess-a", tenant_id="t1")
    assert [i["run_id"] for i in items] == ["run-2", "run-1"]
    got = await store.get("run-1")
    assert got is not None
    assert got["langfuse_url"].endswith("trace-1b")
    # 明确不承载工具轨迹字段
    assert "tool_history" not in got
    assert "arguments" not in got
