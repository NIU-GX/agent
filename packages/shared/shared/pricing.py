"""模型单价表：按 prompt / completion token 估算 USD 成本。"""

from __future__ import annotations

# 单价：USD / 1M tokens（可按合约覆盖）
PRICE_PER_1M: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "text-embedding-3-small": (0.02, 0.0),
    "text-embedding-3-large": (0.13, 0.0),
    "deepseek-chat": (0.14, 0.28),
}


def estimate_cost_usd(
    model: str,
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> float:
    key = model.lower()
    prices = PRICE_PER_1M.get(key)
    if prices is None:
        # 未知模型：按 gpt-4o-mini 近似，避免看板始终为 0
        for known, val in PRICE_PER_1M.items():
            if known in key:
                prices = val
                break
        if prices is None:
            prices = (0.15, 0.60)
    prompt_price, completion_price = prices
    return (prompt_tokens / 1_000_000.0) * prompt_price + (
        completion_tokens / 1_000_000.0
    ) * completion_price
