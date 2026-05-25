from app.config import get_settings


def estimate_cost(prompt_tokens: int, completion_tokens: int) -> float:
    """Estimate USD cost for a Claude Sonnet call.

    Uses per-million-token pricing from settings so it can be overridden per-model
    without code changes.
    """
    s = get_settings()
    input_cost = (prompt_tokens / 1_000_000) * s.price_per_million_input_tokens
    output_cost = (completion_tokens / 1_000_000) * s.price_per_million_output_tokens
    return round(input_cost + output_cost, 6)
