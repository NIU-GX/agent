"""工具调用事前/事中/事后门禁。"""

from __future__ import annotations

import pytest

from agent_core.tools.guard import (
    CallIdempotencyCache,
    check_visibility,
    normalize_result,
    validate_arguments,
)
from agent_core.tools.registry import ToolRegistry


def test_validate_arguments_required_and_type():
    schema = {
        "type": "function",
        "function": {
            "name": "retrieve",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "minLength": 1}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    }
    err = validate_arguments("retrieve", {}, schema)
    assert err is not None
    assert err["error_code"] == "invalid_args"
    assert err["fixable"] is True

    err2 = validate_arguments("retrieve", {"query": ""}, schema)
    assert err2 is not None

    err3 = validate_arguments("retrieve", {"query": "政策", "extra": 1}, schema)
    assert err3 is not None

    assert validate_arguments("retrieve", {"query": "政策"}, schema) is None


def test_check_visibility_locked_and_disabled():
    assert check_visibility(
        name="web_search",
        tier="optional",
        enabled=True,
        registered=True,
        unlocked=set(),
    )["error_code"] == "tool_locked"
    assert check_visibility(
        name="web_search",
        tier="optional",
        enabled=True,
        registered=True,
        unlocked={"web_search"},
    ) is None
    assert check_visibility(
        name="retrieve",
        tier="core",
        enabled=True,
        registered=True,
        unlocked=set(),
    ) is None
    assert check_visibility(
        name="retrieve",
        tier="core",
        enabled=False,
        registered=True,
        unlocked=set(),
    )["error_code"] == "tool_disabled"


def test_normalize_result_empty_retrieval_and_calculator():
    out = normalize_result("retrieve", {"ok": True, "hits": [], "context": ""})
    assert out.get("warning") == "empty_retrieval"
    bad = normalize_result("calculator", {"ok": True})
    assert bad["ok"] is False
    assert bad["error_code"] == "invalid_result"


def test_idempotency_cache():
    cache = CallIdempotencyCache(maxsize=2)
    cache.put("a", {"ok": True, "v": 1})
    assert cache.get("a")["v"] == 1
    cache.put("b", {"ok": True, "v": 2})
    cache.put("c", {"ok": True, "v": 3})
    assert cache.get("a") is None
    assert cache.get("c")["v"] == 3


@pytest.mark.asyncio
async def test_registry_call_gates():
    tools = ToolRegistry()
    locked = await tools.call("web_search", {"query": "x"}, state={"unlocked_tools": []})
    assert locked["error_code"] == "tool_locked"

    bad = await tools.call(
        "retrieve",
        {},
        state={"unlocked_tools": [], "enable_rag": True},
        call_id="c1",
    )
    assert bad["error_code"] == "invalid_args"
    assert bad["fixable"] is True

    replay = await tools.call(
        "retrieve",
        {"query": "should not run"},
        state={"enable_rag": True},
        call_id="c1",
    )
    assert replay.get("idempotent_replay") is True
    assert replay["error_code"] == "invalid_args"

    ok = await tools.call(
        "calculator",
        {"expression": "2+3*4"},
        state={},
        call_id="c2",
    )
    assert ok["ok"] is True
    assert ok["result"] == 14
