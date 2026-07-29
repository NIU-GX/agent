"""LLM 网关：Proxy 一把 sk 路径的纯单元测试。"""

from __future__ import annotations

from llm_gateway import normalize_proxy_base, to_proxy_model


def test_normalize_proxy_base_strips_v1() -> None:
    assert normalize_proxy_base("http://localhost:4000/v1") == "http://localhost:4000"
    assert normalize_proxy_base("http://localhost:4000/v1/") == "http://localhost:4000"
    assert normalize_proxy_base("http://litellm:4000") == "http://litellm:4000"


def test_to_proxy_model_strips_prefix() -> None:
    assert to_proxy_model("gpt-4o-mini") == "gpt-4o-mini"
    assert to_proxy_model("litellm_proxy/gpt-4o-mini") == "gpt-4o-mini"
