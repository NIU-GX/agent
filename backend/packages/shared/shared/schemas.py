"""跨服务共享的 Pydantic Schema（API 入参/出参、Job 载荷等）。"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class DocumentStatus(str, Enum):
    """文档入库状态机（与 RabbitMQ 阶段一一对应）。"""

    UPLOADED = "uploaded"
    PARSING = "parsing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"
    TOMBSTONED = "tombstoned"


class AgentStrategy(str, Enum):
    """Agent 推理策略：企业常见三范式 + 自动路由。"""

    COT = "cot"
    REACT = "react"
    PLAN_EXECUTE = "plan_execute"
    AUTO = "auto"


class RagStage(str, Enum):
    """RAG 入库流水线阶段名（对应 RabbitMQ 队列后缀）。"""

    PARSE = "parse"
    CHUNK = "chunk"
    EMBED = "embed"
    INDEX = "index"


class ChatRequest(BaseModel):
    """对话请求。"""

    message: str = Field(..., min_length=1, description="用户问题")
    session_id: str | None = Field(default=None, description="会话 ID，用于 checkpoint")
    strategy: AgentStrategy = Field(
        default=AgentStrategy.AUTO,
        description="cot | react | plan_execute | auto",
    )
    enable_rag: bool = Field(default=True, description="是否启用知识库检索")
    skills: list[str] = Field(
        default_factory=list,
        description="会话级预激活的 skill 名称（L1）",
    )


class Citation(BaseModel):
    """回答引用片段，强制可追溯。"""

    chunk_id: str
    doc_id: str
    source: str
    text: str
    score: float = 0.0


class ChatEvent(BaseModel):
    """SSE 事件载荷（前端按 type 渲染时间线）。"""

    type: str = Field(
        ...,
        description=(
            "token|thought|tool_start|tool_end|skill_start|skill_end|"
            "plan|citation|final|error|strategy|hitl"
        ),
    )
    data: dict[str, Any] = Field(default_factory=dict)


class DocumentOut(BaseModel):
    """文档列表/详情返回。"""

    id: str
    filename: str
    status: DocumentStatus
    error_message: str | None = None
    chunk_count: int = 0
    created_at: datetime
    updated_at: datetime


class JobOut(BaseModel):
    """异步入库任务进度。"""

    id: str
    doc_id: str
    stage: RagStage | str
    status: str
    attempt: int = 0
    error_message: str | None = None
    updated_at: datetime


class UsageRecord(BaseModel):
    """网关用量记账（简化版）。"""

    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    request_id: str | None = None


class PromptVersionCreate(BaseModel):
    """发布新提示词版本。"""

    content: str = Field(..., min_length=1, description="提示词正文")
    change_note: str | None = Field(default=None, description="变更说明")
    created_by: str | None = Field(default=None, description="操作者")
    activate: bool = Field(default=True, description="是否立即激活为当前版本")


class PromptRollbackRequest(BaseModel):
    """回退到指定历史版本号。"""

    version: int = Field(..., ge=1, description="目标版本号")


class WebhookToolCreate(BaseModel):
    """创建 HTTP Webhook 工具。"""

    name: str = Field(..., min_length=1, max_length=128)
    description: str = Field(default="")
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}},
        description="OpenAI function parameters JSON Schema",
    )
    webhook_url: str = Field(..., min_length=1, max_length=2048)
    webhook_method: str = Field(default="POST")
    webhook_headers: dict[str, Any] = Field(default_factory=dict)
    timeout_sec: float = Field(default=30.0, gt=0, le=300)
    tier: str = Field(default="optional")
    enabled: bool = Field(default=True)


class WebhookToolUpdate(BaseModel):
    """更新 Webhook 工具（仅可变字段）。"""

    description: str | None = None
    parameters: dict[str, Any] | None = None
    webhook_url: str | None = Field(default=None, max_length=2048)
    webhook_method: str | None = None
    webhook_headers: dict[str, Any] | None = None
    timeout_sec: float | None = Field(default=None, gt=0, le=300)
    tier: str | None = None
    enabled: bool | None = None


class SkillCreate(BaseModel):
    """创建 Skill。"""

    name: str = Field(..., min_length=1, max_length=128)
    description: str = Field(default="")
    body: str = Field(default="")
    tools: list[str] = Field(default_factory=list)
    mcp: list[str] = Field(default_factory=list)
    enabled: bool = Field(default=True)


class SkillUpdate(BaseModel):
    """更新 Skill。"""

    description: str | None = None
    body: str | None = None
    tools: list[str] | None = None
    mcp: list[str] | None = None
    enabled: bool | None = None


class McpServerCreate(BaseModel):
    """创建 MCP Server 配置。"""

    name: str = Field(..., min_length=1, max_length=128)
    command: str = Field(..., min_length=1, max_length=512)
    args: list[Any] = Field(default_factory=list)
    env: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = Field(default=True)


class McpServerUpdate(BaseModel):
    """更新 MCP Server 配置。"""

    command: str | None = Field(default=None, max_length=512)
    args: list[Any] | None = None
    env: dict[str, Any] | None = None
    enabled: bool | None = None


class EnabledPatch(BaseModel):
    """通用启用/禁用。"""

    enabled: bool
