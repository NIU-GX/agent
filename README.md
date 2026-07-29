# Agent Platform

企业知识库问答 + 多工具 Agent 平台：统一 LLM 网关、异步 RAG（Milvus + RabbitMQ）、
LangGraph 多策略（CoT / ReAct / Plan-and-Execute）、Tool / MCP / Skill 渐进式披露、
评测与 Compose / Helm 双部署。

## 分层

| 层 | 路径 | 说明 |
|----|------|------|
| Frontend | `frontend/` | Vue3 SPA，只经 `/api/v1` |
| Backend API | `backend/apps/api` | FastAPI：对话 SSE、文档、Eval、能力发现/CRUD、提示词版本 |
| Agent Runtime | `backend/packages/agent-core` | 策略图 + Tool/MCP/Skill |
| RAG Worker | `backend/apps/rag-worker` | 异步入库消费者 |
| Skills | `skills/` | `SKILL.md` 技能包（启动种子，运行期以 DB 为准） |

详见 [docs/architecture.md](docs/architecture.md)。

## 目录结构

```
agent/
├── frontend/                 # Vue3 + Vite 管理台与对话 UI（独立）
├── backend/
│   ├── apps/
│   │   ├── api/              # FastAPI：网关入口、对话、文档、Capabilities、Eval
│   │   └── rag-worker/       # RabbitMQ 消费者：parse/chunk/embed/index
│   └── packages/
│       ├── agent-core/       # LangGraph、Tool/MCP/Skill、checkpoint、HITL
│       ├── rag/              # 入库流水线 + Hybrid 检索
│       ├── llm-gateway/      # 限流 / 熔断 / fallback / 计费
│       ├── eval/             # Retrieval + Generation + Trajectory
│       └── shared/           # 配置、Schema、Postgres 元数据
├── skills/                   # 项目级 Skills（SKILL.md）
├── deploy/
├── docs/
└── scripts/
```

## 快速启动（Compose）

```bash
cp .env.example .env
cp deploy/litellm/.env.example deploy/litellm/.env
# 根 .env：只配 Proxy 地址 / LLM_API_KEY（Proxy sk）/ 模型名
# deploy/litellm/.env：配 LITELLM_MASTER_KEY（与 LLM_API_KEY 相同）+ 厂商真 key

docker compose -f deploy/docker-compose.yml up -d --build
```

- Web: http://localhost:5173
- API: http://localhost:8000/docs
- LiteLLM Proxy: http://localhost:4000
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
cd backend/apps/api && PYTHONPATH=. uvicorn app.main:app --reload --port 8000
# 另开终端
cd frontend && npm install && npm run dev
```

## 策略与能力

| strategy | 含义 |
|----------|------|
| `cot` | Chain-of-Thought |
| `react` | Reason + Act（默认；元工具 + 渐进解锁） |
| `plan_execute` | 先规划再逐步执行（可 HITL） |
| `auto` | LLM Router 自动选择 |

渐进式披露：L0 目录（name/description）→ L1 激活（完整 schema / Skill 正文）→ L2 执行。
能力发现：`GET /api/v1/capabilities/{tools,skills,mcp}`。
能力管理（Postgres Store + 热注入）：`/api/v1/tools`、`/api/v1/skills`、`/api/v1/mcp`（CRUD + enabled）；Web「能力」页可管理。动态 Tool 为 HTTP Webhook。

提示词版本：独立模块（`PromptStore` + `/api/v1/prompts` + Web「提示词」页）；经 `PromptProvider` 端口注入 Agent，二者不互相 import。未注入时 Agent 使用内置默认提示词。

种子配置示例（`.env`，仅首次导入 DB，之后以管理台/API 为准）：

```bash
MCP_SERVERS_JSON=[{"name":"fs","command":"npx","args":["-y","@modelcontextprotocol/server-filesystem","/data"]}]
SKILLS_DIR=skills
```

## License

MIT
