# Kubernetes 部署

```bash
# 1. 构建并推送镜像（示例）
docker build -f deploy/docker/Dockerfile.api -t agent-platform/api:0.1.0 .
docker build -f deploy/docker/Dockerfile.worker -t agent-platform/worker:0.1.0 .
docker build -f deploy/docker/Dockerfile.web -t agent-platform/web:0.1.0 .

# 2. 安装（需集群内已有 Postgres/Redis/RabbitMQ/Milvus，或另装子 chart）
helm upgrade --install agent deploy/helm/agent-platform \
  --namespace agent --create-namespace \
  --set env.LLM_API_KEY=$LLM_API_KEY
```

探针：
- liveness/readiness: `GET /api/v1/health`

扩缩：
- `rag-worker` 配置了 CPU HPA（1–8）
