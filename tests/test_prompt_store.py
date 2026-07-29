"""提示词版本管理独立性 + 与 Agent PromptProvider 端口的装配桥接。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agent_core.prompts import BUILTIN_PROMPT_SEEDS, BUILTIN_PROMPTS, BuiltinPromptProvider
from shared.prompt_store import PromptStore


def test_builtin_provider_independent():
    """Agent 内置 Provider 不依赖 PromptStore。"""
    p = BuiltinPromptProvider()
    assert "逐步推理" in p.get("cot.system")
    assert p.get("missing", "x") == "x"
    assert set(BUILTIN_PROMPTS) == {s["key"] for s in BUILTIN_PROMPT_SEEDS}


def test_prompt_store_has_no_agent_defaults():
    """shared.prompt_store 不再内嵌 Agent 策略正文。"""
    import shared.prompt_store as mod

    assert not hasattr(mod, "DEFAULT_PROMPTS")


@pytest.mark.asyncio
async def test_prompt_store_version_and_rollback():
    pytest.importorskip("aiosqlite")
    from shared.db import Database

    db = Database(dsn="sqlite+aiosqlite:///:memory:")
    await db.create_tables()
    store = PromptStore(db)
    await store.ensure_defaults(BUILTIN_PROMPT_SEEDS)

    assert store.lookup("react.system")
    v2 = await store.create_version(
        "react.system",
        "版本二正文",
        change_note="trial",
        activate=True,
    )
    assert v2["version"] == 2
    assert store.get_active("react.system") == "版本二正文"

    rolled = await store.rollback("react.system", 1)
    assert rolled["active_version"] == 1
    assert rolled["from_version"] == 2
    assert store.get_active("react.system") != "版本二正文"

    detail = await store.get_prompt("react.system")
    assert detail is not None
    assert detail["active_version"] == 1
    assert len(detail["versions"]) >= 2

    v3 = await store.create_version("react.system", "版本三", activate=False)
    assert v3["version"] == 3
    assert store.get_active("react.system") != "版本三"
    await store.rollback("react.system", 3)
    assert store.get_active("react.system") == "版本三"

    await db.aclose()


@pytest.mark.asyncio
async def test_bridge_provider_prefers_store_then_fallback():
    pytest.importorskip("aiosqlite")
    from shared.db import Database

    api_root = Path(__file__).resolve().parents[1] / "backend" / "apps" / "api"
    sys.path.insert(0, str(api_root))
    from app.core.prompt_bridge import PromptStoreProvider  # noqa: E402

    db = Database(dsn="sqlite+aiosqlite:///:memory:")
    await db.create_tables()
    store = PromptStore(db)
    await store.ensure_defaults(
        [{"key": "cot.system", "name": "cot", "description": "", "content": "FROM_STORE"}]
    )
    provider = PromptStoreProvider(store, fallback=BuiltinPromptProvider())
    assert provider.get("cot.system") == "FROM_STORE"
    assert "工具" in provider.get("react.system") or "ReAct" in provider.get("react.system")
    await db.aclose()
