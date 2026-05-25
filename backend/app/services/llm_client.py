"""Unified LLM client. Wraps Anthropic and any OpenAI-compatible provider
(DeepSeek, OpenAI itself, etc.) behind one interface and captures inference
metadata for the logger.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from anthropic import APIError as AnthropicAPIError
from anthropic import AsyncAnthropic
from openai import APIError as OpenAIAPIError
from openai import AsyncOpenAI

from app.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: int
    status: str = "ok"
    error_message: str | None = None


class _BaseClient:
    provider: str
    model: str

    async def chat(
        self, messages: list[dict], system: str | None, max_tokens: int
    ) -> LLMResult:  # pragma: no cover
        raise NotImplementedError


class _AnthropicClient(_BaseClient):
    provider = "anthropic"

    def __init__(self) -> None:
        s = get_settings()
        self.model = s.anthropic_model
        self._client = AsyncAnthropic(api_key=s.anthropic_api_key)

    async def chat(self, messages, system, max_tokens):
        started = time.perf_counter()
        try:
            kwargs: dict = {
                "model": self.model,
                "max_tokens": max_tokens,
                "messages": messages,
            }
            if system:
                kwargs["system"] = system
            r = await self._client.messages.create(**kwargs)
            latency_ms = int((time.perf_counter() - started) * 1000)
            text = "".join(b.text for b in r.content if b.type == "text").strip()
            return LLMResult(
                text=text,
                provider=self.provider,
                model=self.model,
                prompt_tokens=r.usage.input_tokens,
                completion_tokens=r.usage.output_tokens,
                total_tokens=r.usage.input_tokens + r.usage.output_tokens,
                latency_ms=latency_ms,
            )
        except (AnthropicAPIError, Exception) as exc:
            return _error_result(self.provider, self.model, started, exc)


class _OpenAICompatibleClient(_BaseClient):
    """Works for OpenAI itself and any OpenAI-compatible API (DeepSeek, etc.)."""

    def __init__(self) -> None:
        s = get_settings()
        self.provider = s.llm_provider.lower()
        self.model = s.resolved_model()
        self._client = AsyncOpenAI(
            api_key=s.resolved_api_key(),
            base_url=s.resolved_base_url(),
        )

    async def chat(self, messages, system, max_tokens):
        # OpenAI's chat API takes system as a message, not a separate field.
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend(messages)

        started = time.perf_counter()
        try:
            r = await self._client.chat.completions.create(
                model=self.model,
                messages=msgs,
                max_tokens=max_tokens,
            )
            latency_ms = int((time.perf_counter() - started) * 1000)
            text = (r.choices[0].message.content or "").strip()
            usage = r.usage
            return LLMResult(
                text=text,
                provider=self.provider,
                model=self.model,
                prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                total_tokens=getattr(usage, "total_tokens", 0) or 0,
                latency_ms=latency_ms,
            )
        except (OpenAIAPIError, Exception) as exc:
            return _error_result(self.provider, self.model, started, exc)


def _error_result(provider: str, model: str, started: float, exc: BaseException) -> LLMResult:
    if isinstance(exc, asyncio.CancelledError):
        raise exc
    latency_ms = int((time.perf_counter() - started) * 1000)
    logger.exception("LLM call failed (provider=%s)", provider)
    return LLMResult(
        text="",
        provider=provider,
        model=model,
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        latency_ms=latency_ms,
        status="error",
        error_message=str(exc),
    )


_singleton: _BaseClient | None = None


def get_llm_client() -> _BaseClient:
    global _singleton
    if _singleton is None:
        provider = get_settings().llm_provider.lower()
        if provider == "anthropic":
            _singleton = _AnthropicClient()
        elif provider in ("openai", "deepseek"):
            _singleton = _OpenAICompatibleClient()
        else:
            raise ValueError(f"unknown LLM_PROVIDER: {provider}")
    return _singleton
