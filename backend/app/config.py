from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-4-20250514"

    database_url: str = "postgresql+asyncpg://ollive:ollive@localhost:5432/ollive"

    aggregation_interval_seconds: int = 60

    allowed_origins: str = "http://localhost:3000"

    # Claude Sonnet 4 pricing (USD per 1M tokens). Override via env if needed.
    price_per_million_input_tokens: float = 3.00
    price_per_million_output_tokens: float = 15.00

    max_history_messages: int = 20

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
