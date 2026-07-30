"""本地冒烟：验证核心包可导入（不连外部服务）。"""

from __future__ import annotations


def main() -> None:
    from shared.config import settings
    from shared.schemas import AgentStrategy, RagStage
    from shared.pricing import estimate_cost_usd
    from llm_gateway import LLMGateway
    from rag.retrieve import RetrieveService
    from rag.sparse import bm25_sparse
    from agent_core.strategies import build_cot_graph, build_react_graph, build_plan_execute_graph
    from agent_core.skills import SkillRegistry
    from agent_core.tools import ToolRegistry
    from eval.metrics.generation import deepeval_generation_scores, faithfulness_heuristic
    from eval.runners.generation_eval import run_generation_eval
    from eval.thresholds import parse_fail_under
    from agent_core.tracing import langfuse_ready, start_trace
    import deepeval  # noqa: F401 — generation 依赖

    assert settings.app_env
    assert AgentStrategy.REACT.value == "react"
    assert RagStage.PARSE.value == "parse"
    assert faithfulness_heuristic("hello world", "hello") > 0
    assert callable(run_generation_eval)
    assert callable(deepeval_generation_scores)
    assert parse_fail_under("hit_at_k=1.0")["hit_at_k"] == 1.0
    assert deepeval.__version__ or True
    assert langfuse_ready() is False or isinstance(langfuse_ready(), bool)
    ctx = start_trace(session_id="smoke", strategy="react")
    assert ctx.trace_id
    ctx.flush()
    assert estimate_cost_usd("gpt-4o-mini", prompt_tokens=1000) > 0
    assert bm25_sparse("知识库检索")
    assert callable(build_cot_graph)
    assert callable(build_react_graph)
    assert callable(build_plan_execute_graph)
    assert LLMGateway is not None
    assert RetrieveService is not None
    skills = SkillRegistry(settings.skills_dir, load_filesystem=True)
    tools = ToolRegistry(skills=skills)
    assert any(t["name"] == "retrieve" for t in tools.catalog())
    assert "activate_skill" in [
        s["function"]["name"] for s in tools.openai_tools_schema(unlocked=set())
    ]
    assert "http_get" not in [
        s["function"]["name"] for s in tools.openai_tools_schema(unlocked=set())
    ]
    # 延迟导入：确认 ORM 模型可加载
    from shared.db import Database, PostgresStatusStore  # noqa: F401
    from shared.prompt_store import PromptStore  # noqa: F401
    from shared.tool_store import ToolStore  # noqa: F401
    from shared.skill_store import SkillStore  # noqa: F401
    from shared.mcp_store import McpStore  # noqa: F401
    from agent_core.prompts import BuiltinPromptProvider, BUILTIN_PROMPT_SEEDS

    assert BuiltinPromptProvider().get("cot.system")
    assert len(BUILTIN_PROMPT_SEEDS) >= 5
    assert PromptStore is not None
    assert ToolStore is not None
    assert SkillStore is not None
    assert McpStore is not None

    print("smoke ok")


if __name__ == "__main__":
    main()
