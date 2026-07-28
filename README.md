# Agent Platform

企业知识库问答 + 多工具 Agent 平台：统一 LLM 网关、异步 RAG（Milvus + RabbitMQ）、
LangGraph 多策略（CoT / ReAct / Plan-and-Execute）、评测与 Compose / Helm 双部署。

## 目录结构

```
agent/
├── apps/
│   ├── api/              # FastAPI：网关入口、对话、文档、Eval API
│   ├── web/              # Vue3 + Vite 管理台与对话 UI
│   └── rag-worker/       # RabbitMQ 消费者：parse/chunk/embed/index
├── packages/
│   ├── agent-core/       # LangGraph 策略、checkpoint、HITL、MCP
│   ├── rag/              # 入库流水线 + Hybrid 检索
│   ├── llm-gateway/      # 限流 / 熔断 / fallback / 计费
│   ├── eval/             # Retrieval + Generation + Trajectory
│   └── shared/           # 配置、Schema、Postgres 元数据
├── deploy/
│   ├── docker/
│   ├── docker-compose.yml
│   └── helm/agent-platform/
├── docs/
└── scripts/
```

## 快速启动（Compose）

```bash
cp .env.example .env
# 编辑 .env，填入 LLM_API_KEY 或兼容端点

docker compose -f deploy/docker-compose.yml up -d --build
```

- Web: http://localhost:5173
- API: http://localhost:8000/docs
- RabbitMQ Management: http://localhost:15672 （guest/guest）

## 环境要求

- Python **3.9+**（推荐 3.10+；Docker 镜像使用 3.12）
- Node.js 20+（前端）
- Docker / Docker Compose（推荐全栈：Postgres / Redis / RabbitMQ / MinIO / Milvus）

## 本地开发

```bash
bash scripts/bootstrap.sh
source .venv/bin/activate
python scripts/smoke_import.py
cd apps/api && PYTHONPATH=. uvicorn app.main:app --reload --port 8000
# 另开终端
cd apps/web && npm install && npm run dev
```

## 策略说明

| strategy | 含义 |
|----------|------|
| `cot` | Chain-of-Thought |
| `react` | Reason + Act 工具循环（默认） |
| `plan_execute` | 先规划再逐步执行（可 HITL） |
| `auto` | LLM Router 自动选择 |

## License

MIT
