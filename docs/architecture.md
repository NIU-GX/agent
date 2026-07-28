# 架构说明

## 1. 总体

企业知识库问答 Agent：统一 LLM 网关 → LangGraph 多策略编排 → 异步 RAG（RabbitMQ + Milvus）→ Eval → Compose/Helm。

## 2. 技术选型

| 决策 | 理由 |
|------|------|
| LangGraph | 表达 CoT/ReAct/Plan-Execute，checkpoint / HITL |
| RabbitMQ | 入库工作队列，DLX / 重试成熟 |
| Milvus | dense + sparse Hybrid + RRF |
| Postgres | 文档/Job/用量元数据 + LangGraph checkpoint |
| Redis | 网关分布式限流（不做任务队列） |
| MinIO | 原文与解析中间产物 |
| Vue3 SPA | 管理台与对话 SSE |
| FastAPI | 异步 API，与 Python Agent 生态一致 |

## 3. Agent 三范式

1. **CoT**：显式推理轨迹，适合解释/分析；检索一次后纯推理
2. **ReAct**：Thought→Action→Observation；`max_iterations` + 重复工具检测
3. **Plan-and-Execute**：先计划后执行；可 HITL 审计划；单步可嵌套检索/计算

企业可控能力：限步、checkpoint 恢复、人工介入、SSE 可观测、Critic 审查。

## 4. RAG 异步解耦

API 上传 → MinIO → 发布 `rag.parse`；Worker：parse → chunk → embed → index（写 Milvus）。
状态回写 Postgres，API/Worker 共享。Retrieve：rewrite → hybrid → RRF → LLM/lexical rerank → context。

## 5. 网关分层

LiteLLM / httpx 适配厂商；自研层做 Redis 限流、熔断、fallback、用量落库与计价。

## 6. Eval

Retrieval（Hit@k/MRR）→ Generation（LLM-as-judge + 启发式）→ Trajectory（工具/步数/成功率）。

## 7. 部署

Compose 拉起全依赖栈；Helm 提供副本、探针、HPA（worker）。生产关闭内存兜底，强制 API Key。
