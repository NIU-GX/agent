"""RAG Worker：消费 RabbitMQ 四阶段队列，写入 MinIO / Milvus，状态回写 Postgres。"""

from __future__ import annotations

import asyncio
import json
import os

from llm_gateway import LLMGateway, build_rate_limiter
from rag.ingest import run_chunk, run_embed, run_index, run_parse
from rag.models import QueueMessage
from rag.mq import OutboxDispatcher, RagConsumer, connect_rabbitmq
from rag.store import MinioObjectStore, MilvusVectorStore, OpenSearchLexicalStore
from shared.config import settings
from shared.db import Database, PostgresStatusStore, PostgresUsageRecorder
from shared.logging import get_logger
from shared.rag_store import ProductionRagStore
from shared.schemas import RagStage

logger = get_logger(__name__)


async def main() -> None:
    stage_env = os.getenv("STAGE", "all").lower()
    db = Database()
    await db.ensure_schema()
    status = PostgresStatusStore(db)
    rag_store = ProductionRagStore(db)
    await rag_store.bootstrap_legacy()
    usage = PostgresUsageRecorder(db)
    rate_limiter = await build_rate_limiter()
    llm = LLMGateway(rate_limiter=rate_limiter, usage_recorder=usage)

    store = MinioObjectStore()
    vector_store = MilvusVectorStore()
    vector_store.connect()
    lexical_store = OpenSearchLexicalStore() if settings.opensearch_url else None
    if lexical_store:
        await lexical_store.ensure_index()
    logger.info("worker infra ready: minio + milvus + postgres")

    class _NoopStatus:
        async def set_document_status(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            return None

        async def set_job_stage(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            return None

    noop_status = _NoopStatus()

    async def _process(msg: QueueMessage, operation) -> bool:
        """Outbox owns transition; consumer acknowledgement never advances a skipped event."""
        claimed = await rag_store.claim_stage(msg.job_id)
        if claimed is None:
            logger.info("ignore duplicate or leased stage run=%s", msg.job_id)
            return False
        msg.attempt = int(claimed["attempt"])
        try:
            timeout = settings.rag_parser_timeout_seconds if msg.stage == RagStage.PARSE else None
            if timeout:
                await asyncio.wait_for(operation(msg), timeout=timeout)
            else:
                await operation(msg)
            chunk_count = None
            if msg.stage == RagStage.INDEX:
                raw = json.loads((await store.get_bytes(msg.payload_ref)).decode("utf-8"))
                chunk_count = sum(1 for item in raw if (item.get("metadata") or {}).get("kind") == "child")
            await rag_store.complete_stage(
                run_id=msg.job_id,
                payload_ref=msg.payload_ref,
                content_hash=msg.content_hash,
                chunk_count=chunk_count,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("stage failed run=%s", msg.job_id)
            await rag_store.fail_stage(run_id=msg.job_id, error=str(exc), retryable=True)
        return False

    async def handle_parse(msg: QueueMessage) -> bool:
        return await _process(msg, lambda m: run_parse(m, store=store, status=noop_status))

    async def handle_chunk(msg: QueueMessage) -> bool:
        async def operation(m: QueueMessage) -> None:
            await run_chunk(m, store=store, status=noop_status)
            raw = json.loads((await store.get_bytes(m.payload_ref)).decode("utf-8"))
            await rag_store.save_parent_chunks(m.document_version_id or m.doc_id, raw)

        return await _process(msg, operation)

    async def handle_embed(msg: QueueMessage) -> bool:
        return await _process(
            msg,
            lambda m: run_embed(
                m, store=store, embedder=llm, status=noop_status, batch_size=settings.embed_batch_size
            ),
        )

    async def handle_index(msg: QueueMessage) -> bool:
        async def operation(m: QueueMessage) -> None:
            await run_index(m, store=store, vector_store=vector_store, status=noop_status)
            if lexical_store:
                raw = json.loads((await store.get_bytes(m.payload_ref)).decode("utf-8"))
                from rag.models import Chunk

                children = [
                    Chunk.model_validate(item)
                    for item in raw
                    if (item.get("metadata") or {}).get("kind") == "child"
                ]
                await lexical_store.upsert_chunks(children)

        return await _process(
            msg, operation
        )

    handlers = {
        RagStage.PARSE: handle_parse,
        RagStage.CHUNK: handle_chunk,
        RagStage.EMBED: handle_embed,
        RagStage.INDEX: handle_index,
    }

    stages = list(RagStage) if stage_env == "all" else [RagStage(value.strip()) for value in stage_env.split(",")]
    conn = await connect_rabbitmq()
    logger.info("worker connected, stages=%s", [s.value for s in stages])

    tasks = [asyncio.create_task(OutboxDispatcher(store=rag_store).run(conn))] + [
        asyncio.create_task(RagConsumer(stage=stage, handler=handlers[stage]).run(conn))
        for stage in stages
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        await llm.aclose()
        await db.aclose()
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
