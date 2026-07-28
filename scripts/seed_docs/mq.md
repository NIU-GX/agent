# RabbitMQ 与 RAG 流水线

RabbitMQ 用于异步解耦文档入库：parse、chunk、embed、index 分队列消费。
失败消息进入 DLQ（死信队列），支持重试与运维告警。Redis 只做限流缓存，不承担任务队列。
