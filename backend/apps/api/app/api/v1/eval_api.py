"""Eval API：触发检索 / 生成 / 轨迹评测（生成层使用 DeepEval）。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from eval.metrics.generation import deepeval_generation_scores
from eval.metrics.trajectory_deepeval import deepeval_tool_correctness
from eval.runners.agent_eval import run_agent_trajectory_eval
from eval.runners.generation_eval import run_generation_eval
from eval.runners.rag_eval import run_retrieval_eval
from shared.schemas import AgentStrategy

from app.core.security import require_api_key

router = APIRouter()


class EvalRunRequest(BaseModel):
    kind: str = Field(description="retrieval | trajectory | generation")
    dataset: str = Field(default="rag_qa.jsonl")


@router.post("/eval/runs")
async def run_eval(body: EvalRunRequest, request: Request, _: None = Depends(require_api_key)):
    container = request.app.state.container
    import eval as eval_pkg

    ds_dir = Path(eval_pkg.__file__).parent / "datasets"
    path = ds_dir / body.dataset
    if not path.exists() and body.kind != "trajectory":
        raise HTTPException(status_code=404, detail=f"dataset not found: {path.name}")

    if body.kind == "retrieval":

        async def _retrieve(q: str):
            return await container.retrieve.retrieve(q)

        result = await run_retrieval_eval(path, _retrieve)
        return {
            "kind": "retrieval",
            "engine": "builtin",
            "hit_at_k": result.hit_at_k,
            "recall_at_k": result.recall_at_k,
            "mrr": result.mrr,
            "n": result.n,
            "details": result.details,
        }

    if body.kind == "generation":

        async def _retrieve(q: str):
            return await container.retrieve.retrieve(q)

        async def _run(row: dict, context: str) -> str:
            answer_parts: list[str] = []
            async for ev in container.agent.run_stream(
                message=row["question"],
                strategy=AgentStrategy.REACT,
                enable_rag=True,
            ):
                if ev.type == "final":
                    answer_parts.append(str(ev.data.get("answer") or ""))
            return answer_parts[-1] if answer_parts else ""

        async def _judge(*, question: str, answer: str, context: str) -> tuple[float, float]:
            return await deepeval_generation_scores(
                question=question,
                answer=answer,
                context=context,
                gateway=container.llm,
            )

        result = await run_generation_eval(path, _retrieve, _run, _judge)
        return {
            "kind": "generation",
            "engine": "deepeval",
            "faithfulness": result.faithfulness,
            "relevancy": result.relevancy,
            "n": result.n,
            "details": result.details,
        }

    if body.kind == "trajectory":

        async def _run(row: dict):
            tools_used: list[str] = []
            skills_used: list[str] = []
            answer = ""
            strategy = AgentStrategy(row.get("strategy") or "react")
            async for ev in container.agent.run_stream(
                message=row["question"],
                strategy=strategy,
                enable_rag=True,
            ):
                if ev.type == "tool_start":
                    name = ev.data.get("name")
                    if name:
                        tools_used.append(str(name))
                if ev.type == "skill_start":
                    name = ev.data.get("name")
                    if name and name not in skills_used:
                        skills_used.append(str(name))
                if ev.type == "final":
                    answer = ev.data.get("answer") or ""

            expected = list(row.get("expected_tools") or [])
            de_score = await deepeval_tool_correctness(
                question=row["question"],
                answer=str(answer or ""),
                tools_used=tools_used,
                expected_tools=expected,
                gateway=container.llm,
            )
            # 若 DeepEval 给出分数，用其覆盖 tools_used 语义由 runner 再算；
            # 这里把分数塞进返回，runner 仍用 subset 规则；API 层额外返回 deepeval_tool_score 均值。
            return {
                "answer": answer,
                "tools_used": tools_used,
                "skills_used": skills_used,
                "steps": len(tools_used),
                "deepeval_tool_correctness": de_score,
            }

        tpath = ds_dir / (
            body.dataset if body.dataset != "rag_qa.jsonl" else "agent_tasks.jsonl"
        )
        if not tpath.exists():
            raise HTTPException(status_code=404, detail=f"dataset not found: {tpath.name}")
        result = await run_agent_trajectory_eval(tpath, _run)
        de_scores = [
            float(d["deepeval_tool_correctness"])
            for d in result.details
            if d.get("deepeval_tool_correctness") is not None
        ]
        return {
            "kind": "trajectory",
            "engine": "deepeval+builtin",
            "success_rate": result.success_rate,
            "avg_steps": result.avg_steps,
            "tool_accuracy": result.tool_accuracy,
            "skill_accuracy": result.skill_accuracy,
            "deepeval_tool_correctness": (
                sum(de_scores) / len(de_scores) if de_scores else None
            ),
            "n": result.n,
            "details": result.details,
        }

    raise HTTPException(status_code=400, detail=f"unknown kind: {body.kind}")
