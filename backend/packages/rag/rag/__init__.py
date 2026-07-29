"""RAG 包：异步入库流水线（RabbitMQ）+ 可编排同步检索。

Ingest 与 Retrieve 刻意解耦：
- Ingest：追求吞吐、可重试、可水平扩展 Worker
- Retrieve：追求在线延迟，供 Agent 同步调用
"""

from rag.retrieve.service import RetrieveService

__all__ = ["RetrieveService"]
