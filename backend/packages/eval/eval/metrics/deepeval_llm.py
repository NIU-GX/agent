"""DeepEval 评判模型：经业务 LLMGateway（LiteLLM Proxy）统一出站。"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import re
from typing import Any, Optional, Type, Union

from pydantic import BaseModel
from shared.config import settings
from shared.logging import get_logger

logger = get_logger(__name__)


def _extract_json(content: str) -> Any:
    text = (content or "").strip()
    if not text:
        raise ValueError("empty LLM content")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}|\[.*\]", text, re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def _gateway_llm_class() -> type:
    from deepeval.models.base_model import DeepEvalBaseLLM

    class GatewayDeepEvalLLM(DeepEvalBaseLLM):
        def __init__(self, gateway: Any, *, model: str | None = None) -> None:
            self._gateway = gateway
            self._model_name = model or settings.llm_chat_model
            super().__init__(model=self._model_name)

        def load_model(self) -> Any:
            return self._gateway

        def get_model_name(self) -> str:
            return self._model_name

        def generate(
            self, prompt: str, schema: Optional[Type[BaseModel]] = None
        ) -> Union[str, BaseModel]:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return asyncio.run(self.a_generate(prompt, schema=schema))
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(
                    lambda: asyncio.run(self.a_generate(prompt, schema=schema))
                ).result()

        async def a_generate(
            self, prompt: str, schema: Optional[Type[BaseModel]] = None
        ) -> Union[str, BaseModel]:
            body = await self._gateway.chat(
                [{"role": "user", "content": prompt}],
                model=self._model_name,
                temperature=0.0,
            )
            content = body["choices"][0]["message"]["content"]
            if schema is None:
                return content
            data = _extract_json(content)
            if isinstance(schema, type) and issubclass(schema, BaseModel):
                return schema.model_validate(data)
            return content

    return GatewayDeepEvalLLM


def build_deepeval_model(gateway: Any | None = None, *, model: str | None = None) -> Any:
    """构建 DeepEval 评判模型。优先包装业务 gateway。"""
    if gateway is not None:
        cls = _gateway_llm_class()
        return cls(gateway, model=model)

    from deepeval.models import LiteLLMModel

    name = model or settings.llm_chat_model
    litellm_name = name if "/" in name else f"openai/{name}"
    return LiteLLMModel(
        model=litellm_name,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        temperature=0.0,
    )
