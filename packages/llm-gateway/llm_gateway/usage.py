"""用量记账：内存实现；生产由 PostgresUsageRecorder 注入。"""

from __future__ import annotations

from shared.logging import get_logger
from shared.pricing import estimate_cost_usd
from shared.schemas import UsageRecord

logger = get_logger(__name__)


class UsageRecorder:
    """记录每次 LLM 调用的 token 用量，供成本看板聚合。"""

    def __init__(self) -> None:
        self._records: list[UsageRecord] = []

    async def record(self, record: UsageRecord) -> None:
        if record.cost_usd <= 0 and (record.prompt_tokens or record.completion_tokens):
            record.cost_usd = estimate_cost_usd(
                record.model,
                prompt_tokens=record.prompt_tokens,
                completion_tokens=record.completion_tokens,
            )
        self._records.append(record)
        logger.debug(
            "usage model=%s total_tokens=%s cost_usd=%.6f",
            record.model,
            record.total_tokens,
            record.cost_usd,
        )

    def list_recent(self, limit: int = 100) -> list[UsageRecord]:
        return self._records[-limit:]

    def summary(self) -> dict[str, int | float]:
        total_tokens = sum(r.total_tokens for r in self._records)
        return {
            "calls": len(self._records),
            "total_tokens": total_tokens,
            "cost_usd": round(sum(r.cost_usd for r in self._records), 6),
        }
