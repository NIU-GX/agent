"""RabbitMQ 拓扑与发布/消费封装。

队列拓扑（与技术方案一致）：
  Exchange: rag.direct (direct)
  Queues:   rag.parse / rag.chunk / rag.embed / rag.index / rag.dlq
  每业务队列绑定 DLX -> rag.dlq
"""

from __future__ import annotations

import json
import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import aio_pika
from aio_pika import ExchangeType, IncomingMessage, Message

from shared.config import settings
from shared.logging import get_logger
from shared.schemas import RagStage

from rag.models import QueueMessage

logger = get_logger(__name__)

EXCHANGE_NAME = "rag.direct"
DLX_EXCHANGE = "rag.dlx"
DLQ_NAME = "rag.dlq"

# 阶段 → 队列名
STAGE_QUEUE: dict[RagStage, str] = {
    RagStage.PARSE: "rag.parse",
    RagStage.CHUNK: "rag.chunk",
    RagStage.EMBED: "rag.embed",
    RagStage.INDEX: "rag.index",
}

# 流水线下一阶段（index 之后结束）
NEXT_STAGE: dict[RagStage, RagStage | None] = {
    RagStage.PARSE: RagStage.CHUNK,
    RagStage.CHUNK: RagStage.EMBED,
    RagStage.EMBED: RagStage.INDEX,
    RagStage.INDEX: None,
}


async def connect_rabbitmq() -> aio_pika.RobustConnection:
    """建立可自动重连的 RabbitMQ 连接。"""
    return await aio_pika.connect_robust(settings.rabbitmq_url)


async def declare_topology(channel: aio_pika.Channel) -> aio_pika.Exchange:
    """声明 exchange / queue / DLX，幂等可重复调用。"""
    # 死信交换器
    dlx = await channel.declare_exchange(DLX_EXCHANGE, ExchangeType.DIRECT, durable=True)
    dlq = await channel.declare_queue(DLQ_NAME, durable=True)
    await dlq.bind(dlx, routing_key=DLQ_NAME)

    exchange = await channel.declare_exchange(EXCHANGE_NAME, ExchangeType.DIRECT, durable=True)

    for stage, queue_name in STAGE_QUEUE.items():
        queue = await channel.declare_queue(
            queue_name,
            durable=True,
            arguments={
                # 消息被 reject/nack(requeue=False) 或 TTL 到期后进入 DLX
                "x-dead-letter-exchange": DLX_EXCHANGE,
                "x-dead-letter-routing-key": DLQ_NAME,
            },
        )
        await queue.bind(exchange, routing_key=queue_name)
        logger.info("declared queue=%s stage=%s", queue_name, stage)

    return exchange


class RagPublisher:
    """API / Worker 共用的消息发布器。"""

    def __init__(self, channel: aio_pika.Channel, exchange: aio_pika.Exchange) -> None:
        self.channel = channel
        self.exchange = exchange

    async def publish(self, message: QueueMessage) -> None:
        queue_name = STAGE_QUEUE[message.stage]
        body = message.model_dump_json().encode("utf-8")
        await self.exchange.publish(
            Message(
                body=body,
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                headers={"x-attempt": message.attempt},
                message_id=message.outbox_event_id,
            ),
            routing_key=queue_name,
            mandatory=True,
        )
        logger.info(
            "published job_id=%s doc_id=%s stage=%s attempt=%s",
            message.job_id,
            message.doc_id,
            message.stage,
            message.attempt,
        )


class OutboxDispatcher:
    """轮询 Postgres Outbox；发布确认后才标记事件完成。"""

    def __init__(self, *, store: Any, poll_seconds: float = 0.5) -> None:
        self.store = store
        self.poll_seconds = poll_seconds

    async def run(self, connection: aio_pika.RobustConnection) -> None:
        channel = await connection.channel(publisher_confirms=True)
        exchange = await declare_topology(channel)
        publisher = RagPublisher(channel, exchange)
        while True:
            events = await self.store.pending_outbox()
            if not events:
                await asyncio.sleep(self.poll_seconds)
                continue
            for event in events:
                try:
                    payload = dict(event["payload"])
                    payload["outbox_event_id"] = event["id"]
                    await publisher.publish(QueueMessage.model_validate(payload))
                    await self.store.mark_outbox_published(event["id"])
                except Exception as exc:  # noqa: BLE001
                    logger.exception("outbox publish failed event=%s", event["id"])
                    await self.store.mark_outbox_failed(event["id"], str(exc))


ConsumerHandler = Callable[[QueueMessage], Awaitable[bool | None]]


class RagConsumer:
    """按阶段消费队列；业务成功 ack，异常超重试则进 DLQ。"""

    def __init__(
        self,
        *,
        stage: RagStage,
        handler: ConsumerHandler,
        max_retries: int | None = None,
    ) -> None:
        self.stage = stage
        self.handler = handler
        self.max_retries = max_retries or settings.rag_max_retries
        self.queue_name = STAGE_QUEUE[stage]

    async def run(self, connection: aio_pika.RobustConnection) -> None:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=4)
        exchange = await declare_topology(channel)
        publisher = RagPublisher(channel, exchange)
        queue = await channel.get_queue(self.queue_name)

        async with queue.iterator() as queue_iter:
            async for incoming in queue_iter:
                await self._handle_one(incoming, publisher)

    async def _handle_one(
        self,
        incoming: IncomingMessage,
        publisher: RagPublisher,
    ) -> None:
        async with incoming.process(requeue=False):
            raw: dict[str, Any] = json.loads(incoming.body.decode("utf-8"))
            msg = QueueMessage.model_validate(raw)
            try:
                advance = await self.handler(msg)
                # 成功后推进下一阶段
                nxt = NEXT_STAGE.get(self.stage)
                if nxt is not None and advance is not False:
                    await publisher.publish(
                        QueueMessage(
                            job_id=msg.job_id,
                            doc_id=msg.doc_id,
                            stage=nxt,
                            payload_ref=msg.payload_ref,
                            attempt=0,
                            content_hash=msg.content_hash,
                        )
                    )
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "stage=%s job_id=%s failed: %s",
                    self.stage,
                    msg.job_id,
                    exc,
                )
                if msg.attempt + 1 < self.max_retries:
                    # 重新入当前队列，attempt+1
                    await publisher.publish(
                        QueueMessage(
                            job_id=msg.job_id,
                            doc_id=msg.doc_id,
                            stage=self.stage,
                            payload_ref=msg.payload_ref,
                            attempt=msg.attempt + 1,
                            content_hash=msg.content_hash,
                        )
                    )
                else:
                    # 超过重试：抛出以触发 nack -> DLQ（process requeue=False）
                    raise
