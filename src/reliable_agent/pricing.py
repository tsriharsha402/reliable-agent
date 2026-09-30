"""USD per million tokens, Anthropic first-party API, September 2026.

Check https://www.anthropic.com/pricing before relying on these for budgeting.
"""

from __future__ import annotations

PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


def cost_usd(model: str, input_tokens: int, output_tokens: int, fallback_model: str) -> float:
    input_price, output_price = PRICES_PER_MTOK.get(model) or PRICES_PER_MTOK[fallback_model]
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000
