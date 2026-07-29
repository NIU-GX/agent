"""网关核心客户端：经 LiteLLM Proxy 统一 chat / embeddings。

业务侧只持有一把 Proxy sk（LLM_API_KEY）+ Proxy 地址（LLM_BASE_URL）；
各厂商真实密钥由 Proxy 侧配置，不进入应用。
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from shared.config import settings
from shared.logging import get_logger
from shared.schemas import UsageRecord

from llm_gateway.circuit import CircuitBreaker
from llm_gateway.rate_limit import TokenBucketRateLimiter
from llm_gateway.usage import UsageRecorder

logger = get_logger(__name__)


def normalize_proxy_base(url: str) -> str:
    """LiteLLM Proxy api_base 不含 /v1 后缀。"""
    base = (url or "").rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3].rstrip("/")
    return base


def to_proxy_model(model: str) -> str:
    """规范化业务模型名（去掉误带的 litellm_proxy/ 前缀）。"""
    name = (model or "").strip()
    if not name:
        raise ValueError("model name is required")
    return name.removeprefix("litellm_proxy/")


class LLMGateway:
    """面向业务的 LLM 统一入口：一律经 LiteLLM Proxy（一把 sk）。"""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        rate_limiter: Any | None = None,
        usage_recorder: UsageRecorder | None = None,
        circuit: CircuitBreaker | None = None,
    ) -> None:
        self.base_url = normalize_proxy_base(base_url or settings.llm_base_url)
        self.api_key = api_key or settings.llm_api_key
        self.rate_limiter = rate_limiter or TokenBucketRateLimiter(
            rpm=settings.gateway_rpm,
            tpm=settings.gateway_tpm,
        )
        self.usage_recorder = usage_recorder or UsageRecorder()
        self.circuit = circuit or CircuitBreaker(
            fail_threshold=settings.gateway_circuit_fail_threshold,
            reset_seconds=settings.gateway_circuit_reset_seconds,
        )

    async def aclose(self) -> None:
        close = getattr(self.rate_limiter, "aclose", None)
        if close:
            await close()

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
    ) -> dict[str, Any]:
        if not self.circuit.allow():
            raise RuntimeError("llm circuit open: upstream temporarily unavailable")
        primary = model or settings.llm_chat_model
        await self.rate_limiter.acquire(tokens=1)
        try:
            body = await self._chat_once(
                messages,
                model=primary,
                temperature=temperature,
                tools=tools,
            )
            self.circuit.record_success()
            return body
        except Exception as exc:  # noqa: BLE001
            self.circuit.record_failure()
            fallback = settings.llm_fallback_chat_model
            if fallback == primary:
                raise
            logger.warning("chat primary failed (%s), fallback to %s", exc, fallback)
            await self.rate_limiter.acquire(tokens=1)
            body = await self._chat_once(
                messages,
                model=fallback,
                temperature=temperature,
                tools=tools,
            )
            self.circuit.record_success()
            return body

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
    ) -> AsyncIterator[str]:
        if not self.circuit.allow():
            raise RuntimeError("llm circuit open: upstream temporarily unavailable")
        model_name = model or settings.llm_chat_model
        await self.rate_limiter.acquire(tokens=1)
        request_id = str(uuid.uuid4())
        try:
            async for chunk in self._litellm_stream(
                messages, model=model_name, temperature=temperature
            ):
                yield chunk
            self.circuit.record_success()
        except Exception:
            self.circuit.record_failure()
            raise
        await self.usage_recorder.record(UsageRecord(model=model_name, request_id=request_id))

    async def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]:
        if not texts:
            return []
        if not self.circuit.allow():
            raise RuntimeError("llm circuit open: upstream temporarily unavailable")
        model_name = model or settings.llm_embed_model
        await self.rate_limiter.acquire(tokens=len(texts))
        try:
            vectors = await self._litellm_embed(texts, model=model_name)
            self.circuit.record_success()
            return vectors
        except Exception:
            self.circuit.record_failure()
            raise

    async def _chat_once(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        temperature: float,
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        return await self._litellm_chat(
            messages, model=model, temperature=temperature, tools=tools
        )

    def _litellm_common_kwargs(self, *, model: str) -> dict[str, Any]:
        # custom_llm_provider=litellm_proxy：强制走 Proxy，避免 SDK 按厂商拆 key
        return {
            "model": to_proxy_model(model),
            "api_key": self.api_key,
            "api_base": self.base_url,
            "custom_llm_provider": "litellm_proxy",
        }

    async def _litellm_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        temperature: float,
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        import litellm

        kwargs: dict[str, Any] = {
            **self._litellm_common_kwargs(model=model),
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        started = time.perf_counter()
        resp = await litellm.acompletion(**kwargs)
        body = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)
        usage = body.get("usage") or {}
        if hasattr(usage, "model_dump"):
            usage = usage.model_dump()
        await self.usage_recorder.record(
            UsageRecord(
                model=model,
                prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                completion_tokens=int(usage.get("completion_tokens", 0) or 0),
                total_tokens=int(usage.get("total_tokens", 0) or 0),
                request_id=str(uuid.uuid4()),
            )
        )
        logger.debug("chat ok model=%s latency_ms=%.1f", model, (time.perf_counter() - started) * 1000)
        return body

    async def _litellm_stream(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        temperature: float,
    ) -> AsyncIterator[str]:
        import litellm

        stream = await litellm.acompletion(
            **self._litellm_common_kwargs(model=model),
            messages=messages,
            temperature=temperature,
            stream=True,
        )
        async for chunk in stream:
            try:
                delta = chunk.choices[0].delta
                content = getattr(delta, "content", None)
                if content:
                    yield content
            except Exception:  # noqa: BLE001
                continue

    async def _litellm_embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        import litellm

        resp = await litellm.aembedding(
            **self._litellm_common_kwargs(model=model),
            input=texts,
        )
        body = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)
        data = body.get("data") or []
        items = sorted(data, key=lambda x: x.get("index", 0))
        usage = body.get("usage") or {}
        if hasattr(usage, "model_dump"):
            usage = usage.model_dump()
        await self.usage_recorder.record(
            UsageRecord(
                model=model,
                prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                total_tokens=int(usage.get("total_tokens", 0) or 0),
                request_id=str(uuid.uuid4()),
            )
        )
        return [item["embedding"] for item in items]
