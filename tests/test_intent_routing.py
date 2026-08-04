"""能力路由与 web_search / retrieve gate 单测。"""

from __future__ import annotations

import pytest

from agent_core.nodes.intent import RoutingPlan, heuristic_routing, _parse_routing_json
from agent_core.tools.registry import ToolRegistry


def test_heuristic_kb_policy():
    plan = heuristic_routing(
        "公司请假政策是什么",
        available_skills=["kb-qa", "calc-assist", "web-research"],
    )
    assert plan.enable_rag is True
    assert "kb-qa" in plan.skills
    assert plan.enable_web_search is False


def test_heuristic_web_search():
    plan = heuristic_routing(
        "帮我搜索一下最新公开行业新闻",
        available_skills=["kb-qa", "web-research"],
    )
    assert plan.enable_web_search is True
    assert "web-research" in plan.skills


def test_heuristic_chitchat_disables_rag():
    plan = heuristic_routing("你好", available_skills=["kb-qa"])
    assert plan.enable_rag is False


def test_heuristic_multi_agent_kb_and_web():
    plan = heuristic_routing(
        "对比知识库政策与网上最新公开解读",
        available_skills=["kb-qa", "web-research"],
    )
    assert plan.enable_rag is True
    assert plan.enable_web_search is True
    assert plan.strategy == "multi_agent"
    assert "rag" in plan.agents
    assert "web" in plan.agents
    assert "synth" in plan.agents


def test_parse_routing_json():
    plan = _parse_routing_json(
        '说明如下 {"enable_rag": false, "enable_web_search": true, '
        '"strategy": "react", "skills": ["web-research"], "agents": [], '
        '"reason": "公网"} 结束',
        available_skills=["web-research", "kb-qa"],
    )
    assert plan is not None
    assert plan.enable_rag is False
    assert plan.enable_web_search is True
    assert plan.strategy == "react"
    assert plan.skills == ["web-research"]


@pytest.mark.asyncio
async def test_retrieve_gated_when_rag_disabled():
    class FakeRetriever:
        async def retrieve(self, query: str, *, scope=None):
            raise AssertionError("should not retrieve")

    tools = ToolRegistry(retriever=FakeRetriever())
    result = await tools.call(
        "retrieve",
        {"query": "q"},
        state={"enable_rag": False},
    )
    assert result["ok"] is True
    assert result.get("skipped") is True
    assert result.get("hits") == []


@pytest.mark.asyncio
async def test_web_search_requires_api_key(monkeypatch):
    from shared import config as cfg

    monkeypatch.setattr(cfg.settings, "web_search_api_key", "")
    monkeypatch.setattr(cfg.settings, "web_search_provider", "tavily")
    tools = ToolRegistry()
    result = await tools.call(
        "web_search",
        {"query": "agent platform"},
        state={"unlocked_tools": ["web_search"]},
    )
    assert result["ok"] is False
    assert "WEB_SEARCH_API_KEY" in result.get("error", "")


@pytest.mark.asyncio
async def test_web_search_tavily_mock(monkeypatch):
    from shared import config as cfg

    monkeypatch.setattr(cfg.settings, "web_search_api_key", "tvly-test")
    monkeypatch.setattr(cfg.settings, "web_search_provider", "tavily")
    monkeypatch.setattr(cfg.settings, "web_search_max_results", 3)

    class FakeResp:
        is_error = False
        status_code = 200
        text = "{}"

        def json(self):
            return {
                "results": [
                    {
                        "title": "Hello",
                        "url": "https://example.com",
                        "content": "snippet",
                    }
                ]
            }

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            assert "tavily.com" in url
            assert json["api_key"] == "tvly-test"
            assert json["query"] == "hello"
            return FakeResp()

    import agent_core.tools.registry as reg

    monkeypatch.setattr(reg.httpx, "AsyncClient", FakeClient)
    tools = ToolRegistry()
    result = await tools.call(
        "web_search",
        {"query": "hello"},
        state={"unlocked_tools": ["web_search"]},
    )
    assert result["ok"] is True
    assert result["results"][0]["title"] == "Hello"


def test_routing_plan_to_dict():
    plan = RoutingPlan(enable_rag=True, strategy="cot", reason="x")
    d = plan.to_dict()
    assert d["enable_rag"] is True
    assert d["strategy"] == "cot"
