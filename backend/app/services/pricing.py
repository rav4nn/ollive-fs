from __future__ import annotations

from app.config import DEFAULT_PRICING, get_settings


def estimate_cost(
    prompt_tokens: int, completion_tokens: int, provider: str | None = None
) -> float:
    """Estimate USD cost for a single LLM call.

    Resolution order:
      1. PRICE_PER_MILLION_*_TOKENS env overrides (if both set).
      2. Built-in defaults for `provider` (or the configured default if None).
      3. Zeroes if the provider name isn't recognized.
    """
    s = get_settings()
    if (
        s.price_per_million_input_tokens is not None
        and s.price_per_million_output_tokens is not None
    ):
        in_rate = s.price_per_million_input_tokens
        out_rate = s.price_per_million_output_tokens
    else:
        p = (provider or s.llm_provider).lower()
        in_rate, out_rate = DEFAULT_PRICING.get(p, (0.0, 0.0))

    input_cost = (prompt_tokens / 1_000_000) * in_rate
    output_cost = (completion_tokens / 1_000_000) * out_rate
    return round(input_cost + output_cost, 6)
