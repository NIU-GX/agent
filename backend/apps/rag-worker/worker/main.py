"""RAG Worker：消费 RabbitMQ 四阶段队列，写入 MinIO / Milvus，状态回写 Postgres。"""

from __future__ import annotations

import asyncio
import os

from llm_gateway import LLMGateway, build_rate_limiter
from rag.ingest import run_chunk, run_embed, run_index, run_parse
from rag.models import QueueMessage
from rag.mq import RagConsumer, connect_rabbitmq
from rag.store import MinioObjectStore, MilvusVectorStore
from shared.config import settings
from shared.db import Database, PostgresStatusStore, PostgresUsageRecorder
from shared.logging import get_logger
from shared.schemas import RagStage

logger = get_logger(__name__)


async def main() -> None:
    stage_env = os.getenv("STAGE", "all").lower()
    db = Database()
    await db.create_tables()
    status = PostgresStatusStore(db)
    usage = PostgresUsageRecorder(db)
    rate_limiter = await build_rate_limiter()
    llm = LLMGateway(rate_limiter=rate_limiter, usage_recorder=usage)

    store = MinioObjectStore()
    vector_store = MilvusVectorStore()
    vector_store.connect()
    logger.info("worker infra ready: minio + milvus + postgres")

    async def handle_parse(msg: QueueMessage) -> None:
        if msg.content_hash and not await status.claim_idempotency(
            msg.doc_id, "parse", msg.content_hash
        ):
            logger.info("skip duplicate parse doc_id=%s", msg.doc_id)
            return
        await run_parse(msg, store=store, status=status)

    async def handle_chunk(msg: QueueMessage) -> None:
        key = msg.content_hash or msg.payload_ref
        if key and not await status.claim_idempotency(msg.doc_id, "chunk", key):
            logger.info("skip duplicate chunk doc_id=%s", msg.doc_id)
            return
        await run_chunk(msg, store=store, status=status)

    async def handle_embed(msg: QueueMessage) -> None:
        key = msg.content_hash or msg.payload_ref
        if key and not await status.claim_idempotency(msg.doc_id, "embed", key):
            logger.info("skip duplicate embed doc_id=%s", msg.doc_id)
            return
        await run_embed(
            msg,
            store=store,
            embedder=llm,
            status=status,
            batch_size=settings.embed_batch_size,
        )

    async def handle_index(msg: QueueMessage) -> None:
        key = msg.content_hash or msg.payload_ref
        if key and not await status.claim_idempotency(msg.doc_id, "index", key):
            logger.info("skip duplicate index doc_id=%s", msg.doc_id)
            return
        await run_index(msg, store=store, vector_store=vector_store, status=status)

    handlers = {
        RagStage.PARSE: handle_parse,
        RagStage.CHUNK: handle_chunk,
        RagStage.EMBED: handle_embed,
        RagStage.INDEX: handle_index,
    }

    stages = list(RagStage) if stage_env == "all" else [RagStage(stage_env)]
    conn = await connect_rabbitmq()
    logger.info("worker connected, stages=%s", [s.value for s in stages])

    tasks = [
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
