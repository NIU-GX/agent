"""Eval API：触发检索 / 生成 / 轨迹评测。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from eval.metrics.generation import answer_relevancy_heuristic, faithfulness_llm_judge
from eval.runners.agent_eval import run_agent_trajectory_eval
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
            "hit_at_k": result.hit_at_k,
            "recall_at_k": result.recall_at_k,
            "mrr": result.mrr,
            "n": result.n,
            "details": result.details,
        }

    if body.kind == "generation":
        import json

        rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        details = []
        faith_sum = 0.0
        rel_sum = 0.0
        for row in rows:
            q = row["question"]
            retrieved = await container.retrieve.retrieve(q)
            context = retrieved.context_text
            answer_parts: list[str] = []
            async for ev in container.agent.run_stream(
                message=q,
                strategy=AgentStrategy.REACT,
                enable_rag=True,
            ):
                if ev.type == "final":
                    answer_parts.append(str(ev.data.get("answer") or ""))
            answer = answer_parts[-1] if answer_parts else ""
            faith = await faithfulness_llm_judge(
                container.llm, question=q, answer=answer, context=context
            )
            rel = answer_relevancy_heuristic(answer, q)
            faith_sum += faith
            rel_sum += rel
            details.append(
                {
                    "question": q,
                    "faithfulness": faith,
                    "relevancy": rel,
                    "answer": answer[:500],
                }
            )
        n = max(len(rows), 1)
        return {
            "kind": "generation",
            "faithfulness": faith_sum / n,
            "relevancy": rel_sum / n,
            "n": len(rows),
            "details": details,
        }

    if body.kind == "trajectory":

        async def _run(row: dict):
            tools_used = []
            answer = ""
            strategy = AgentStrategy(row.get("strategy") or "react")
            async for ev in container.agent.run_stream(
                message=row["question"],
                strategy=strategy,
                enable_rag=True,
            ):
                if ev.type == "tool_start":
                    tools_used.append(ev.data.get("name"))
                if ev.type == "final":
                    answer = ev.data.get("answer") or ""
            return {"answer": answer, "tools_used": tools_used, "steps": len(tools_used)}

        tpath = ds_dir / (
            body.dataset if body.dataset != "rag_qa.jsonl" else "agent_tasks.jsonl"
        )
        if not tpath.exists():
            raise HTTPException(status_code=404, detail=f"dataset not found: {tpath.name}")
        result = await run_agent_trajectory_eval(tpath, _run)
        return {
            "kind": "trajectory",
            "success_rate": result.success_rate,
            "avg_steps": result.avg_steps,
            "tool_accuracy": result.tool_accuracy,
            "n": result.n,
            "details": result.details,
        }

    raise HTTPException(status_code=400, detail=f"unknown kind: {body.kind}")
