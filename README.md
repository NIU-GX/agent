# Agent Platform

企业知识库问答 + 多工具 Agent 平台：统一 LLM 网关、异步 RAG（Milvus + RabbitMQ）、
LangGraph 多策略（CoT / ReAct / Plan-and-Execute / Multi-Agent）、意图能力路由、
联网检索（web_search）、Tool / MCP / Skill 渐进式披露、
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

## Langfuse 全链路追踪（可选）

独立 Compose 栈（不占用主栈 PG/Redis 端口）：

```bash
make langfuse-up
# UI: http://localhost:3000
# 预置账号: admin@localhost / langfuse-admin
# 预置 key: pk-lf-local-dev / sk-lf-local-dev
```

根 `.env` 打开上报：

```bash
LANGFUSE_ENABLED=true
LANGFUSE_HOST=http://localhost:3000
LANGFUSE_PUBLIC_URL=http://localhost:3000
LANGFUSE_PUBLIC_KEY=pk-lf-local-dev
LANGFUSE_SECRET_KEY=sk-lf-local-dev
```

前端 `VITE_LANGFUSE_URL`（默认 `http://localhost:3000`）用于导航「追踪」外链；对话 SSE 会带 `run_id` / `trace_id` / `langfuse_url`。
完整工具轨迹以 Langfuse 为准；业务库 `agent_runs` 只存指针，可用 `GET /api/v1/chat/runs?session_id=` 查询。
工具正确性：`ToolRegistry.call` 做解锁/JSON Schema/幂等门禁，结果统一 `ok`+可修复 `error_code`/`hint`；详见 `docs/architecture.md` §3。

## 评测与 CI

生成质量使用 **DeepEval**（`FaithfulnessMetric` / `AnswerRelevancyMetric`），经 LiteLLM Proxy 作评判模型；
Trajectory 可选 `ToolCorrectnessMetric`。CI / `make eval` 仍走 **mock**（不调真实 LLM）。

```bash
make eval    # mock harness + 阈值门禁（无外网）
make test    # pytest
make smoke   # 导入冒烟
# 真实 DeepEval：启动 API 后 POST /api/v1/eval/runs {"kind":"generation"}
```

GitHub Actions：`smoke` → `pytest` → `scripts/run_eval.py --mode mock`。

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
| `multi_agent` | Supervisor + rag/web/calc 专员汇总 |
| `auto` | 能力路由自动选择策略 / RAG / 联网 / Skills |

对话前能力路由会产出 `enable_rag`、`enable_web_search`、预激活 Skills 与子智能体列表；
前端 RAG 默认 `Auto`（`enable_rag=null`），可手动覆盖。联网检索需配置 `WEB_SEARCH_API_KEY`（Tavily）。

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
