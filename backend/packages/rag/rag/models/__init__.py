"""RAG 领域模型：文档块、检索结果、队列消息载荷。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from shared.schemas import RagStage


class Chunk(BaseModel):
    """切分后的文本块，入库与检索的基本单位。"""

    chunk_id: str
    doc_id: str
    parent_id: str | None = None
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    # dense / sparse 在 embed / index 阶段填充
    dense_vector: list[float] | None = None
    sparse_vector: dict[int, float] | None = None


class ParsedDocument(BaseModel):
    """Parse 阶段产出：纯文本 + 可选结构信息。"""

    doc_id: str
    filename: str
    text: str
    sections: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievalHit(BaseModel):
    """单条召回结果。"""

    chunk_id: str
    doc_id: str
    text: str
    source: str = ""
    score: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)
    parent_id: str | None = None
    tenant_id: str | None = None
    kb_id: str | None = None
    document_version_id: str | None = None


class RetrievalResult(BaseModel):
    """检索编排最终输出，直接喂给 Agent / Eval。"""

    query: str
    hits: list[RetrievalHit] = Field(default_factory=list)
    rewritten_queries: list[str] = Field(default_factory=list)
    context_text: str = ""


class QueueMessage(BaseModel):
    """RabbitMQ 消息体：大对象只放 MinIO，这里只带引用。"""

    job_id: str
    doc_id: str
    stage: RagStage
    payload_ref: str = Field(description="MinIO object key 或阶段中间产物路径")
    attempt: int = 0
    content_hash: str | None = None
    document_version_id: str | None = None
    tenant_id: str | None = None
    kb_id: str | None = None
    outbox_event_id: str | None = None
