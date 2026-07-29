"""装配层桥接：将独立 PromptStore 适配为 Agent 的 PromptProvider 端口。

本模块不 import agent_core，避免双向依赖；fallback 由调用方注入（鸭子类型 .get）。
"""

from __future__ import annotations

from typing import Any, Optional

from shared.prompt_store import PromptStore


class PromptStoreProvider:
    """优先读版本库激活正文；缺失时回退 fallback（通常为 BuiltinPromptProvider）。"""

    def __init__(self, store: PromptStore, fallback: Optional[Any] = None) -> None:
        self._store = store
        self._fallback = fallback

    def get(self, key: str, default: str = "") -> str:
        cached = self._store.lookup(key)
        if cached is not None:
            return cached
        if self._fallback is not None:
            return self._fallback.get(key, default)
        return default
