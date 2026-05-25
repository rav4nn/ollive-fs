"""Thin wrapper around the Anthropic SDK that captures inference metadata.

Returns both the assistant text and a metadata dict (latency, token counts, status).
The caller is responsible for persisting both the message and the log entry.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from anthropic import AsyncAnthropic, APIError

from app.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class ClaudeResult:
    text: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: int
    model: str
    status: str = "ok"
    error_message: str | None = None


class ClaudeClient:
    def __init__(self) -> None:
        settings = get_settings()
        self._model = settings.claude_model
        self._client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def chat(
        self,
        messages: list[dict],
        system: str | None = None,
        max_tokens: int = 1024,
    ) -> ClaudeResult:
        """Send a chat completion request. Captures timing and token usage even on error."""
        started = time.perf_counter()
        try:
            kwargs: dict = {
                "model": self._model,
                "max_tokens": max_tokens,
                "messages": messages,
            }
            if system:
                kwargs["system"] = system
            response = await self._client.messages.create(**kwargs)
            latency_ms = int((time.perf_counter() - started) * 1000)

            text_parts = [block.text for block in response.content if block.type == "text"]
            text = "".join(text_parts).strip()

            return ClaudeResult(
                text=text,
                prompt_tokens=response.usage.input_tokens,
                completion_tokens=response.usage.output_tokens,
                total_tokens=response.usage.input_tokens + response.usage.output_tokens,
                latency_ms=latency_ms,
                model=self._model,
            )
        except APIError as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            logger.exception("Claude API error")
            return ClaudeResult(
                text="",
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                latency_ms=latency_ms,
                model=self._model,
                status="error",
                error_message=str(exc),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover
            latency_ms = int((time.perf_counter() - started) * 1000)
            logger.exception("Unexpected error calling Claude")
            return ClaudeResult(
                text="",
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                latency_ms=latency_ms,
                model=self._model,
                status="error",
                error_message=str(exc),
            )


_singleton: ClaudeClient | None = None


def get_claude_client() -> ClaudeClient:
    global _singleton
    if _singleton is None:
        _singleton = ClaudeClient()
    return _singleton
