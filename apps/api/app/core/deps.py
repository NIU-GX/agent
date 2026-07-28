"""依赖注入：Postgres / Redis / MinIO / Milvus / RabbitMQ / Agent 统一装配。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aio_pika

from agent_core import AgentRuntime
from agent_core.tools import ToolRegistry
from llm_gateway import LLMGateway, build_rate_limiter
from rag.mq import RagPublisher, declare_topology
from rag.retrieve import RetrieveService
from rag.store import InMemoryVectorStore, MinioObjectStore, MilvusVectorStore
from shared.config import settings
from shared.db import Database, PostgresStatusStore, PostgresUsageRecorder
from shared.logging import get_logger

logger = get_logger(__name__)


@dataclass
class AppState:
    llm: LLMGateway
    vector_store: Any
    object_store: Any
    retrieve: RetrieveService
    tools: ToolRegistry
    agent: AgentRuntime
    status: PostgresStatusStore
    db: Database
    rabbit_conn: aio_pika.RobustConnection | None = None
    publisher: RagPublisher | None = None

    async def aclose(self) -> None:
        await self.llm.aclose()
        if self.rabbit_conn:
            await self.rabbit_conn.close()
        await self.db.aclose()
        close_cp = getattr(self.agent, "aclose", None)
        if close_cp:
            await close_cp()


_STATE: AppState | None = None


class _RetrieveAdapter:
    def __init__(self, service: RetrieveService) -> None:
        self.service = service

    async def retrieve(self, query: str):
        return await self.service.retrieve(query)


async def _build_vector_store() -> Any:
    store = MilvusVectorStore()
    try:
        store.connect()
        logger.info("milvus connected host=%s", settings.milvus_host)
        return store
    except Exception as exc:  # noqa: BLE001
        if settings.allow_inmemory_fallback or settings.app_env == "dev":
            logger.error(
                "milvus unavailable (%s); using InMemoryVectorStore — "
                "API and worker will NOT share index. Set ALLOW_INMEMORY_FALLBACK=false in prod.",
                exc,
            )
            return InMemoryVectorStore()
        raise RuntimeError(f"milvus required: {exc}") from exc


async def _build_object_store() -> Any:
    try:
        store = MinioObjectStore()
        logger.info("minio connected endpoint=%s", settings.minio_endpoint)
        return store
    except Exception as exc:  # noqa: BLE001
        if settings.allow_inmemory_fallback or settings.app_env == "dev":
            from pathlib import Path

            class _Local:
                def __init__(self) -> None:
                    self.root = Path("data/blobs")
                    self.root.mkdir(parents=True, exist_ok=True)

                async def get_bytes(self, key: str) -> bytes:
                    path = Path(key)
                    if not path.is_file():
                        path = self.root / key
                    return path.read_bytes()

                async def put_bytes(
                    self, key: str, data: bytes, content_type: str = "application/octet-stream"
                ) -> None:
                    path = self.root / key
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)

            logger.error("minio unavailable (%s); falling back to local data/blobs", exc)
            return _Local()
        raise RuntimeError(f"minio required: {exc}") from exc


async def get_app_state() -> AppState:
    global _STATE
    if _STATE is not None:
        return _STATE

    db = Database()
    await db.create_tables()
    status = PostgresStatusStore(db)
    usage = PostgresUsageRecorder(db)
    rate_limiter = await build_rate_limiter()
    llm = LLMGateway(rate_limiter=rate_limiter, usage_recorder=usage)

    vector_store = await _build_vector_store()
    object_store = await _build_object_store()

    class _Dense:
        async def search(self, vector, top_k):
            return await vector_store.search(vector, top_k)

    class _Sparse:
        async def search(self, sparse, top_k):
            return await vector_store.sparse_search(sparse, top_k)

    retrieve = RetrieveService(
        dense=_Dense(),
        sparse=_Sparse(),
        embedder=llm,
        chat=llm,
    )
    tools = ToolRegistry(retriever=_RetrieveAdapter(retrieve))
    await tools.load_mcp_servers(settings.mcp_servers_json)
    agent = AgentRuntime(llm=llm, tools=tools)
    await agent.setup_checkpointer()

    publisher = None
    rabbit_conn = None
    try:
        rabbit_conn = await aio_pika.connect_robust(settings.rabbitmq_url)
        channel = await rabbit_conn.channel()
        exchange = await declare_topology(channel)
        publisher = RagPublisher(channel, exchange)
        logger.info("rabbitmq connected")
    except Exception as exc:  # noqa: BLE001
        logger.warning("rabbitmq unavailable, document ingest enqueue disabled: %s", exc)

    _STATE = AppState(
        llm=llm,
        vector_store=vector_store,
        object_store=object_store,
        retrieve=retrieve,
        tools=tools,
        agent=agent,
        status=status,
        db=db,
        rabbit_conn=rabbit_conn,
        publisher=publisher,
    )
    return _STATE
