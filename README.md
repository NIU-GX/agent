# Agent Platform

企业知识库问答 + 多工具 Agent 平台：统一 LLM 网关、异步 RAG（Milvus + RabbitMQ）、
LangGraph 多策略（CoT / ReAct / Plan-and-Execute）、Tool / MCP / Skill 渐进式披露、
评测与 Compose / Helm 双部署。

## 分层

| 层 | 路径 | 说明 |
|----|------|------|
| Frontend | `apps/web` | Vue3 SPA，只经 `/api/v1` |
| Backend API | `apps/api` | FastAPI：对话 SSE、文档、Eval、能力发现 |
| Agent Runtime | `packages/agent-core` | 策略图 + Tool/MCP/Skill |
| RAG Worker | `apps/rag-worker` | 异步入库消费者 |
| Skills | `skills/` | `SKILL.md` 技能包（渐进披露） |

详见 [docs/architecture.md](docs/architecture.md)。

## 目录结构

```
agent/
├── apps/
│   ├── api/              # FastAPI：网关入口、对话、文档、Capabilities、Eval
│   ├── web/              # Vue3 + Vite 管理台与对话 UI
│   └── rag-worker/       # RabbitMQ 消费者：parse/chunk/embed/index
├── packages/
│   ├── agent-core/       # LangGraph、Tool/MCP/Skill、checkpoint、HITL
│   ├── rag/              # 入库流水线 + Hybrid 检索
│   ├── llm-gateway/      # 限流 / 熔断 / fallback / 计费
│   ├── eval/             # Retrieval + Generation + Trajectory
│   └── shared/           # 配置、Schema、Postgres 元数据
├── skills/               # 项目级 Skills（SKILL.md）
├── deploy/
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

- Python **3.12+**（与 Docker 镜像 / Ruff `py312` 一致；3.9 已 EOL，不再支持）
- Node.js **22+**（前端；Docker 使用 `node:22-alpine`）
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

## 策略与能力

| strategy | 含义 |
|----------|------|
| `cot` | Chain-of-Thought |
| `react` | Reason + Act（默认；元工具 + 渐进解锁） |
| `plan_execute` | 先规划再逐步执行（可 HITL） |
| `auto` | LLM Router 自动选择 |

渐进式披露：L0 目录（name/description）→ L1 激活（完整 schema / Skill 正文）→ L2 执行。
能力 API：`GET /api/v1/capabilities/{tools,skills,mcp}`。

MCP 配置示例（`.env`）：

```bash
MCP_SERVERS_JSON=[{"name":"fs","command":"npx","args":["-y","@modelcontextprotocol/server-filesystem","/data"]}]
SKILLS_DIR=skills
```

## License

MIT
