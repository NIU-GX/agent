# Langfuse 本地自托管

独立于主 `deploy/docker-compose.yml`，避免 Postgres/Redis 端口冲突。

```bash
make langfuse-up
# UI http://localhost:3000
# 账号 admin@localhost / langfuse-admin
# Key  pk-lf-local-dev / sk-lf-local-dev
```

Host 端口：UI `3000`，PG `5433`，Redis `6380`，ClickHouse `8124`，MinIO `9090`。

业务侧在根 `.env` 设置 `LANGFUSE_ENABLED=true` 与对应 key 后，Agent SSE 会上报并附带 `langfuse_url`。
完整 LLM/tool 轨迹以本服务为准；业务库 `agent_runs` 仅存 `run_id`/`session_id`/`trace_id`/`langfuse_url` 指针。
