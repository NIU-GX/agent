from __future__ import annotations

"""全局配置：从环境变量 / .env 加载，供各服务统一读取。"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用级配置：从环境变量 / .env 加载，供各服务统一读取。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = Field(default="dev", description="运行环境：dev/staging/prod")
    app_api_key: str = Field(default="dev-api-key-change-me", description="API Key 鉴权")
    log_level: str = Field(default="INFO")
    # 基础设施不可用时是否允许内存兜底（仅本地单测建议 true）
    allow_inmemory_fallback: bool = Field(default=False)

    # --- Postgres：业务元数据 + LangGraph checkpoint ---
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "agent"
    postgres_password: str = "agent"
    postgres_db: str = "agent"

    # --- Redis：限流 / 短时缓存（不做任务队列）---
    redis_url: str = "redis://localhost:6379/0"

    # --- RabbitMQ：RAG 异步流水线 ---
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"

    # --- MinIO：原文与解析产物 ---
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "agent-docs"
    minio_secure: bool = False

    # --- Milvus ---
    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_collection: str = "kb_chunks"
    milvus_dim: int = 1536

    # --- LLM OpenAI-compatible / LiteLLM ---
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = "sk-xxx"
    llm_chat_model: str = "gpt-4o-mini"
    llm_embed_model: str = "text-embedding-3-small"
    llm_fallback_chat_model: str = "gpt-4o-mini"
    # 为空则走 httpx 直连；设置如 openai/gpt-4o-mini 则走 LiteLLM
    litellm_model: str = ""
    litellm_fallback_model: str = ""

    gateway_rpm: int = 60
    gateway_tpm: int = 100_000
    gateway_circuit_fail_threshold: int = 5
    gateway_circuit_reset_seconds: float = 30.0
    embed_batch_size: int = 32
    rag_max_retries: int = 3
    rag_rerank_mode: str = Field(default="llm", description="lexical|llm")

    agent_default_strategy: str = "react"
    agent_max_iterations: int = 8
    agent_enable_hitl: bool = False
    agent_enable_checkpoint: bool = True
    mcp_servers_json: str = Field(
        default="[]",
        description='MCP server 列表 JSON，如 [{"name":"fs","command":"npx","args":["-y","@modelcontextprotocol/server-filesystem","/data"]}]',
    )

    @property
    def postgres_dsn(self) -> str:
        """异步 SQLAlchemy / asyncpg 连接串。"""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def postgres_dsn_sync(self) -> str:
        """同步连接串（Alembic / 部分 checkpointer 使用）。"""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    """进程内单例，避免重复解析环境变量。"""
    return Settings()


settings = get_settings()
