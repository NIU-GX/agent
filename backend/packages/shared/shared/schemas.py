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
