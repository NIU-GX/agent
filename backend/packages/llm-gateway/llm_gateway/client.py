"""网关核心客户端：chat / embeddings，带限流、熔断、fallback、LiteLLM 与用量钩子。"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx

from shared.config import settings
from shared.logging import get_logger
from shared.schemas import UsageRecord

from llm_gateway.circuit import CircuitBreaker
from llm_gateway.rate_limit import TokenBucketRateLimiter
from llm_gateway.usage import UsageRecorder

logger = get_logger(__name__)


class LLMGateway:
    """面向业务的 LLM 统一入口。

    - 默认：httpx 直连 OpenAI-compatible
    - 设置 litellm_model：走 LiteLLM 做多厂商适配
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        rate_limiter: Any | None = None,
        usage_recorder: UsageRecorder | None = None,
        circuit: CircuitBreaker | None = None,
    ) -> None:
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
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
        self.use_litellm = bool(settings.litellm_model)
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=httpx.Timeout(120.0, connect=10.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()
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
        primary = model or (settings.litellm_model if self.use_litellm else settings.llm_chat_model)
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
            fallback = settings.litellm_fallback_model or settings.llm_fallback_chat_model
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
        model_name = model or (settings.litellm_model if self.use_litellm else settings.llm_chat_model)
        await self.rate_limiter.acquire(tokens=1)
        request_id = str(uuid.uuid4())
        if self.use_litellm:
            async for chunk in self._litellm_stream(messages, model=model_name, temperature=temperature):
                yield chunk
            await self.usage_recorder.record(UsageRecord(model=model_name, request_id=request_id))
            return

        payload: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        try:
            async with self._client.stream("POST", "/chat/completions", json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if data == "[DONE]":
                        break
                    import json

                    chunk = json.loads(data)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        yield content
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
        if self.use_litellm:
            return await self._litellm_embed(texts, model=model_name)
        resp = await self._client.post(
            "/embeddings",
            json={"model": model_name, "input": texts},
        )
        resp.raise_for_status()
        body = resp.json()
        items = sorted(body["data"], key=lambda x: x["index"])
        usage = body.get("usage", {})
        await self.usage_recorder.record(
            UsageRecord(
                model=model_name,
                prompt_tokens=usage.get("prompt_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
                request_id=str(uuid.uuid4()),
            )
        )
        self.circuit.record_success()
        return [item["embedding"] for item in items]

    async def _chat_once(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        temperature: float,
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        if self.use_litellm:
            return await self._litellm_chat(
                messages, model=model, temperature=temperature, tools=tools
            )
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        started = time.perf_counter()
        resp = await self._client.post("/chat/completions", json=payload)
        resp.raise_for_status()
        body = resp.json()
        usage = body.get("usage", {})
        await self.usage_recorder.record(
            UsageRecord(
                model=model,
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
                request_id=str(uuid.uuid4()),
            )
        )
        logger.debug("chat ok model=%s latency_ms=%.1f", model, (time.perf_counter() - started) * 1000)
        return body

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
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "api_key": self.api_key,
            "api_base": self.base_url if self.base_url else None,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
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
            model=model,
            messages=messages,
            temperature=temperature,
            stream=True,
            api_key=self.api_key,
            api_base=self.base_url if self.base_url else None,
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

        resp = await litellm.aembedding(model=model, input=texts, api_key=self.api_key)
        body = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)
        data = body.get("data") or []
        items = sorted(data, key=lambda x: x.get("index", 0))
        usage = body.get("usage") or {}
        await self.usage_recorder.record(
            UsageRecord(
                model=model,
                prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                total_tokens=int(usage.get("total_tokens", 0) or 0),
                request_id=str(uuid.uuid4()),
            )
        )
        return [item["embedding"] for item in items]
