from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ------ Provider selection ------
    # One of: "anthropic", "deepseek", "openai"
    llm_provider: str = "anthropic"

    # Anthropic
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-20250514"

    # OpenAI-compatible (used for both "openai" and "deepseek")
    openai_api_key: str = ""
    openai_base_url: str | None = None  # e.g. https://api.deepseek.com/v1
    openai_model: str = "gpt-4o-mini"

    # DeepSeek convenience knobs — if set, override the openai_* values when
    # llm_provider == "deepseek". Lets the user just set DEEPSEEK_API_KEY.
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"
    deepseek_base_url: str = "https://api.deepseek.com/v1"

    # ------ Storage ------
    database_url: str = "postgresql+asyncpg://ollive:ollive@localhost:5432/ollive"

    # ------ Ingestion worker ------
    aggregation_interval_seconds: int = 60

    # ------ CORS ------
    allowed_origins: str = "http://localhost:3000"

    # ------ Pricing (USD per 1M tokens) ------
    # Defaults are per-provider. Override via env if you want a different rate.
    price_per_million_input_tokens: float | None = None
    price_per_million_output_tokens: float | None = None

    # ------ Conversation ------
    max_history_messages: int = 20

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    def resolved_model(self) -> str:
        p = self.llm_provider.lower()
        if p == "anthropic":
            return self.anthropic_model
        if p == "deepseek":
            return self.deepseek_model
        if p == "openai":
            return self.openai_model
        raise ValueError(f"unknown LLM_PROVIDER: {self.llm_provider}")

    def resolved_api_key(self) -> str:
        p = self.llm_provider.lower()
        if p == "anthropic":
            return self.anthropic_api_key
        if p == "deepseek":
            return self.deepseek_api_key or self.openai_api_key
        if p == "openai":
            return self.openai_api_key
        raise ValueError(f"unknown LLM_PROVIDER: {self.llm_provider}")

    def resolved_base_url(self) -> str | None:
        p = self.llm_provider.lower()
        if p == "deepseek":
            return self.openai_base_url or self.deepseek_base_url
        if p == "openai":
            return self.openai_base_url
        return None


# Pricing defaults per provider (USD per 1M tokens). Used when env doesn't override.
DEFAULT_PRICING: dict[str, tuple[float, float]] = {
    # provider -> (input, output)
    "anthropic": (3.00, 15.00),     # Claude Sonnet 4
    "deepseek": (0.27, 1.10),       # deepseek-chat (cache-miss)
    "openai": (0.15, 0.60),         # gpt-4o-mini
}


@lru_cache
def get_settings() -> Settings:
    return Settings()
