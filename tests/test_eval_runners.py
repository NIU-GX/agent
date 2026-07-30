"""Eval harness 单元测试：确定性 mock，不依赖外部 LLM / 向量库。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.runners.agent_eval import run_agent_trajectory_eval
from eval.runners.generation_eval import run_generation_eval
from eval.runners.rag_eval import run_retrieval_eval
from eval.thresholds import check_thresholds, parse_fail_under

ROOT = Path(__file__).resolve().parents[1]
DS_DIR = ROOT / "backend/packages/eval/eval/datasets"


class _Hit:
    def __init__(self, doc_id: str) -> None:
        self.doc_id = doc_id


class _RetrieveResult:
    def __init__(self, hits: list[_Hit], context_text: str = "") -> None:
        self.hits = hits
        self.context_text = context_text


def test_parse_fail_under():
    assert parse_fail_under("hit_at_k=1.0,success_rate=0.8") == {
        "hit_at_k": 1.0,
        "success_rate": 0.8,
    }
    assert parse_fail_under("") == {}
    assert parse_fail_under(None) == {}


def test_check_thresholds_pass_and_fail():
    assert check_thresholds({"hit_at_k": 1.0}, {"hit_at_k": 1.0}) == []
    fails = check_thresholds({"hit_at_k": 0.5}, {"hit_at_k": 1.0})
    assert fails and "hit_at_k" in fails[0]


@pytest.mark.asyncio
async def test_retrieval_eval_perfect_fixture(tmp_path: Path):
    ds = tmp_path / "rag.jsonl"
    ds.write_text(
        json.dumps({"question": "q1", "golden_doc_ids": ["doc_a", "doc_b"]}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )

    async def retrieve(_q: str):
        return _RetrieveResult(hits=[_Hit("doc_b"), _Hit("other")], context_text="ctx")

    result = await run_retrieval_eval(ds, retrieve, k=5)
    assert result.n == 1
    assert result.hit_at_k == 1.0
    assert result.mrr == 1.0
    assert result.recall_at_k == 0.5


@pytest.mark.asyncio
async def test_trajectory_tool_and_skill_accuracy(tmp_path: Path):
    ds = tmp_path / "tasks.jsonl"
    rows = [
        {
            "question": "q1",
            "expected_tools": ["retrieve"],
            "expected_skills": ["kb-qa"],
            "success_contains": "ok",
        },
        {
            "question": "q2",
            "expected_tools": [],
            "expected_skills": [],
            "success_contains": "done",
        },
    ]
    ds.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")

    async def run_fn(row: dict):
        return {
            "answer": f"answer {row['success_contains']}",
            "tools_used": list(row.get("expected_tools") or []),
            "skills_used": list(row.get("expected_skills") or []),
            "steps": 1,
        }

    result = await run_agent_trajectory_eval(ds, run_fn)
    assert result.n == 2
    assert result.success_rate == 1.0
    assert result.tool_accuracy == 1.0
    assert result.skill_accuracy == 1.0


@pytest.mark.asyncio
async def test_generation_eval_aggregates(tmp_path: Path):
    ds = tmp_path / "gen.jsonl"
    ds.write_text(
        json.dumps({"question": "hello world"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    async def retrieve(_q: str):
        return _RetrieveResult(hits=[], context_text="hello world context")

    async def run_fn(row: dict, context: str) -> str:
        return f"{context} {row['question']}"

    async def judge_fn(*, question: str, answer: str, context: str):
        return 0.8, 0.6

    result = await run_generation_eval(ds, retrieve, run_fn, judge_fn)
    assert result.n == 1
    assert result.faithfulness == 0.8
    assert result.relevancy == 0.6


@pytest.mark.asyncio
async def test_deepeval_generation_scores_uses_metrics(monkeypatch: pytest.MonkeyPatch):
    from eval.metrics import generation as gen_mod

    class _FakeMetric:
        def __init__(self, *args, **kwargs):
            self._score = 0.91 if kwargs.get("model") is not None else 0.5

        async def a_measure(self, test_case, **kwargs):
            # Faithfulness 与 Relevancy 用不同分数区分
            name = self.__class__.__name__
            return 0.91 if "Faith" in name or not hasattr(self, "_kind") else 0.77

    class _Faith(_FakeMetric):
        _kind = "faith"

        async def a_measure(self, test_case, **kwargs):
            return 0.91

    class _Rel(_FakeMetric):
        _kind = "rel"

        async def a_measure(self, test_case, **kwargs):
            return 0.77

    monkeypatch.setattr(gen_mod, "build_deepeval_model", lambda gateway=None, model=None: object())
    import deepeval.metrics as dem

    monkeypatch.setattr(dem, "FaithfulnessMetric", _Faith)
    monkeypatch.setattr(dem, "AnswerRelevancyMetric", _Rel)

    faith, rel = await gen_mod.deepeval_generation_scores(
        question="q",
        answer="a",
        context="c",
        gateway=object(),
    )
    assert faith == 0.91
    assert rel == 0.77


@pytest.mark.asyncio
async def test_deepeval_generation_fallback_on_error(monkeypatch: pytest.MonkeyPatch):
    from eval.metrics import generation as gen_mod

    def _boom(*_a, **_k):
        raise RuntimeError("no judge")

    monkeypatch.setattr(gen_mod, "build_deepeval_model", _boom)
    faith, rel = await gen_mod.deepeval_generation_scores(
        question="hello world",
        answer="hello world",
        context="hello world",
        gateway=object(),
    )
    assert faith > 0
    assert rel > 0


@pytest.mark.asyncio
async def test_shipped_datasets_mock_pass():
    """与 CI / make eval 同源：对仓库内数据集跑确定性 fixture。"""

    async def retrieve_factory(dataset: Path):
        by_q = {
            json.loads(line)["question"]: json.loads(line)
            for line in dataset.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }

        async def retrieve(q: str):
            row = by_q.get(q) or {}
            ids = list(row.get("golden_doc_ids") or [])
            return _RetrieveResult(hits=[_Hit(i) for i in ids], context_text=" ".join(ids))

        return retrieve

    rag = DS_DIR / "rag_qa.jsonl"
    ret = await run_retrieval_eval(rag, await retrieve_factory(rag))
    assert ret.hit_at_k == 1.0

    tasks = DS_DIR / "agent_tasks.jsonl"

    async def run_fn(row: dict):
        return {
            "answer": f"x {row.get('success_contains')}",
            "tools_used": list(row.get("expected_tools") or []),
            "skills_used": list(row.get("expected_skills") or []),
            "steps": 1,
        }

    traj = await run_agent_trajectory_eval(tasks, run_fn)
    assert traj.success_rate == 1.0
    assert traj.skill_accuracy == 1.0
