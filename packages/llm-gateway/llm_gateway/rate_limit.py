"""令牌桶限流：优先 Redis 分布式，失败时回退进程内。"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from shared.config import settings
from shared.logging import get_logger

logger = get_logger(__name__)


class TokenBucketRateLimiter:
    """进程内令牌桶（单实例兜底）。"""

    def __init__(self, *, rpm: int, tpm: int) -> None:
        self.rpm = max(rpm, 1)
        self.tpm = max(tpm, 1)
        self._capacity = float(self.rpm)
        self._tokens = float(self.rpm)
        self._refill_per_sec = self.rpm / 60.0
        self._updated_at = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: int = 1) -> None:
        need = float(max(tokens, 1))
        async with self._lock:
            while True:
                self._refill()
                if self._tokens >= need:
                    self._tokens -= need
                    return
                deficit = need - self._tokens
                wait = deficit / self._refill_per_sec
                await asyncio.sleep(min(wait, 1.0))

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._updated_at
        self._updated_at = now
        self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_per_sec)


class RedisRateLimiter:
    """基于 Redis Lua 的滑动窗口 RPM 限流（多副本安全）。"""

    def __init__(
        self,
        *,
        redis_url: str | None = None,
        rpm: int | None = None,
        tpm: int | None = None,
        key_prefix: str = "gw:rl",
    ) -> None:
        self.redis_url = redis_url or settings.redis_url
        self.rpm = max(rpm or settings.gateway_rpm, 1)
        self.tpm = max(tpm or settings.gateway_tpm, 1)
        self.key_prefix = key_prefix
        self._redis: Any = None
        self._fallback = TokenBucketRateLimiter(rpm=self.rpm, tpm=self.tpm)
        self._script = """
        local key = KEYS[1]
        local now = tonumber(ARGV[1])
        local window = tonumber(ARGV[2])
        local limit = tonumber(ARGV[3])
        local cost = tonumber(ARGV[4])
        redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
        local current = redis.call('ZCARD', key)
        if current + cost > limit then
          return 0
        end
        for i = 1, cost do
          redis.call('ZADD', key, now, now .. '-' .. i .. '-' .. math.random())
        end
        redis.call('PEXPIRE', key, window)
        return 1
        """

    async def _client(self) -> Any:
        if self._redis is None:
            from redis.asyncio import Redis

            self._redis = Redis.from_url(self.redis_url, decode_responses=True)
        return self._redis

    async def aclose(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    async def acquire(self, tokens: int = 1) -> None:
        cost = max(tokens, 1)
        try:
            client = await self._client()
            while True:
                now_ms = int(time.time() * 1000)
                ok = await client.eval(
                    self._script,
                    1,
                    f"{self.key_prefix}:rpm",
                    now_ms,
                    60_000,
                    self.rpm,
                    cost,
                )
                if int(ok) == 1:
                    return
                await asyncio.sleep(0.05)
        except Exception as exc:  # noqa: BLE001
            logger.warning("redis rate limit unavailable, fallback in-process: %s", exc)
            await self._fallback.acquire(tokens=cost)


async def build_rate_limiter() -> RedisRateLimiter | TokenBucketRateLimiter:
    """优先 Redis；连接失败时返回进程内限流器。"""
    limiter = RedisRateLimiter()
    try:
        client = await limiter._client()
        await client.ping()
        logger.info("redis rate limiter ready")
        return limiter
    except Exception as exc:  # noqa: BLE001
        logger.warning("redis unavailable for rate limit: %s", exc)
        return TokenBucketRateLimiter(rpm=settings.gateway_rpm, tpm=settings.gateway_tpm)
