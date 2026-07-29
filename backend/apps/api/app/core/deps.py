"""依赖注入：Postgres / Redis / MinIO / Milvus / RabbitMQ / Agent 统一装配。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aio_pika

from agent_core import AgentRuntime
from agent_core.prompts import BUILTIN_PROMPT_SEEDS, BuiltinPromptProvider
from agent_core.skills import SkillRegistry, filesystem_skill_seeds
from agent_core.tools import ToolRegistry
from llm_gateway import LLMGateway, build_rate_limiter
from rag.mq import RagPublisher, declare_topology
from rag.retrieve import RetrievalScope, RetrieveService
from rag.store import InMemoryVectorStore, MinioObjectStore, MilvusVectorStore, OpenSearchLexicalStore
from rag.retrieve.reranker import HttpReranker
from shared.config import settings
from shared.db import Database, PostgresStatusStore, PostgresUsageRecorder
from shared.logging import get_logger
from shared.rag_store import ProductionRagStore
from shared.mcp_store import McpStore
from shared.prompt_store import PromptStore
from shared.skill_store import SkillStore
from shared.tool_store import ToolStore

from app.core.capability_bridge import CapabilitySync
from app.core.prompt_bridge import PromptStoreProvider

logger = get_logger(__name__)


@dataclass
class AppState:
    llm: LLMGateway
    vector_store: Any
    object_store: Any
    retrieve: RetrieveService
    tools: ToolRegistry
    skills: SkillRegistry
    agent: AgentRuntime
    status: PostgresStatusStore
    db: Database
    prompts: PromptStore
    tool_store: ToolStore
    skill_store: SkillStore
    mcp_store: McpStore
    capability_sync: CapabilitySync
    rag_store: ProductionRagStore
    rabbit_conn: aio_pika.RobustConnection | None = None
    publisher: RagPublisher | None = None

    async def aclose(self) -> None:
        await self.llm.aclose()
        await self.tools.aclose()
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

    async def retrieve(self, query: str, *, scope: dict[str, Any] | None = None):
        resolved = None
        if scope:
            kb_ids = frozenset(scope.get("kb_ids") or [])
            tenant_id = str(scope.get("tenant_id") or "")
            resolved = RetrievalScope(
                tenant_id=tenant_id,
                kb_ids=kb_ids,
                active_version_ids=frozenset(scope.get("active_version_ids") or []),
            )
        return await self.service.retrieve(query, scope=resolved)


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
    await db.ensure_schema()
    status = PostgresStatusStore(db)
    rag_store = ProductionRagStore(db)
    await rag_store.bootstrap_legacy()
    # 提示词管理为独立能力；仅在此装配层与 Agent 端口对接
    prompts = PromptStore(db)
    await prompts.ensure_defaults(BUILTIN_PROMPT_SEEDS)
    prompt_provider = PromptStoreProvider(prompts, fallback=BuiltinPromptProvider())

    tool_store = ToolStore(db)
    skill_store = SkillStore(db)
    mcp_store = McpStore(db)
    await skill_store.ensure_defaults(filesystem_skill_seeds(settings.skills_dir))
    await mcp_store.ensure_defaults(McpStore.parse_servers_json(settings.mcp_servers_json))

    usage = PostgresUsageRecorder(db)
    rate_limiter = await build_rate_limiter()
    llm = LLMGateway(rate_limiter=rate_limiter, usage_recorder=usage)

    vector_store = await _build_vector_store()
    object_store = await _build_object_store()

    class _Dense:
        async def search(self, vector, top_k, *, scope=None):
            return await vector_store.search(
                vector, top_k, tenant_id=getattr(scope, "tenant_id", None), kb_ids=set(getattr(scope, "kb_ids", set()))
            )

    class _Sparse:
        async def search(self, sparse, top_k, *, scope=None):
            return await vector_store.sparse_search(
                sparse, top_k, tenant_id=getattr(scope, "tenant_id", None), kb_ids=set(getattr(scope, "kb_ids", set()))
            )

    class _Parents:
        async def hydrate(self, hits, scope):
            return await rag_store.hydrate_parent_hits(
                hits, active_version_ids=set(scope.active_version_ids)
            )

    lexical = OpenSearchLexicalStore() if settings.opensearch_url else None
    if lexical:
        await lexical.ensure_index()
    reranker = HttpReranker(settings.reranker_url) if settings.reranker_url else None

    retrieve = RetrieveService(
        dense=_Dense(),
        sparse=_Sparse(),
        embedder=llm,
        chat=llm,
        parent_hydrator=_Parents(),
        lexical=lexical,
        reranker=reranker,
    )
    hosts = [h.strip() for h in (settings.allowed_http_hosts or "").split(",") if h.strip()]
    skills = SkillRegistry(settings.skills_dir, load_filesystem=False)
    tools = ToolRegistry(
        retriever=_RetrieveAdapter(retrieve),
        allowed_http_hosts=hosts,
        skills=skills,
    )
    capability_sync = CapabilitySync(
        tools=tools,
        skills=skills,
        tool_store=tool_store,
        skill_store=skill_store,
        mcp_store=mcp_store,
    )
    await capability_sync.sync_all()
    agent = AgentRuntime(
        llm=llm, tools=tools, skills=skills, prompt_provider=prompt_provider
    )
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
        skills=skills,
        agent=agent,
        status=status,
        db=db,
        prompts=prompts,
        tool_store=tool_store,
        skill_store=skill_store,
        mcp_store=mcp_store,
        capability_sync=capability_sync,
        rag_store=rag_store,
        rabbit_conn=rabbit_conn,
        publisher=publisher,
    )
    return _STATE
