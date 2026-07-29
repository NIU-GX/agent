# Kubernetes 部署

```bash
# 1. 构建并推送镜像（示例）
docker build -f deploy/docker/Dockerfile.api -t agent-platform/api:0.1.0 .
docker build -f deploy/docker/Dockerfile.worker -t agent-platform/worker:0.1.0 .
docker build -f deploy/docker/Dockerfile.web -t agent-platform/web:0.1.0 .

# 2. 安装（需集群内已有 Postgres/Redis/RabbitMQ/Milvus，或另装子 chart）
# Chart 自带 LiteLLM Proxy；业务只持有 LITELLM_MASTER_KEY 这一把 sk
helm upgrade --install agent deploy/helm/agent-platform \
  --namespace agent --create-namespace \
  --set-string secrets.LITELLM_MASTER_KEY="$LITELLM_MASTER_KEY" \
  --set-string secrets.OPENAI_API_KEY="$OPENAI_API_KEY"
```

说明：
- 业务 `LLM_BASE_URL` 默认指向 `http://<release>-litellm:4000`
- `LLM_API_KEY` 与 `LITELLM_MASTER_KEY` 应为同一把 Proxy sk
- 厂商模型清单见 `deploy/helm/agent-platform/litellm-config.yaml`

探针：
- API liveness/readiness: `GET /api/v1/health`
- LiteLLM: `/health/liveliness`、`/health/readiness`

扩缩：
- `rag-worker` 配置了 CPU HPA（1–8）
