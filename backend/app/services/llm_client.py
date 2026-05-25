"""Unified LLM client. Wraps Anthropic and any OpenAI-compatible provider
(DeepSeek, OpenAI itself, etc.) behind one interface and captures inference
metadata for the logger.

Each provider exposes two entry points:
  - chat()         -> waits for the full response, returns LLMResult
  - chat_stream()  -> async-yields text chunks, then yields a final
                      LLMResult with token + latency metadata
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Union

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
    # Optional: time from request start to first streamed token. None for
    # non-streaming calls.
    time_to_first_token_ms: int | None = None
    status: str = "ok"
    error_message: str | None = None


# A streaming call yields zero or more text chunks (str) and then one
# terminal LLMResult that includes the full text and usage metadata.
StreamItem = Union[str, LLMResult]


class _BaseClient:
    provider: str
    model: str

    async def chat(
        self, messages: list[dict], system: str | None, max_tokens: int
    ) -> LLMResult:  # pragma: no cover
        raise NotImplementedError

    def chat_stream(
        self, messages: list[dict], system: str | None, max_tokens: int
    ) -> AsyncIterator[StreamItem]:  # pragma: no cover
        raise NotImplementedError


# ----------------------------------------------------------------------------
# Anthropic
# ----------------------------------------------------------------------------


class _AnthropicClient(_BaseClient):
    provider = "anthropic"

    def __init__(self) -> None:
        s = get_settings()
        self.model = s.anthropic_model
        self._client = AsyncAnthropic(api_key=s.anthropic_api_key)

    def _kwargs(self, messages, system, max_tokens) -> dict:
        kwargs: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        return kwargs

    async def chat(self, messages, system, max_tokens):
        started = time.perf_counter()
        try:
            r = await self._client.messages.create(**self._kwargs(messages, system, max_tokens))
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

    async def chat_stream(self, messages, system, max_tokens):
        started = time.perf_counter()
        ttft_ms: int | None = None
        chunks: list[str] = []
        try:
            async with self._client.messages.stream(
                **self._kwargs(messages, system, max_tokens)
            ) as stream:
                async for text in stream.text_stream:
                    if ttft_ms is None:
                        ttft_ms = int((time.perf_counter() - started) * 1000)
                    chunks.append(text)
                    yield text
                final = await stream.get_final_message()
            latency_ms = int((time.perf_counter() - started) * 1000)
            yield LLMResult(
                text="".join(chunks).strip(),
                provider=self.provider,
                model=self.model,
                prompt_tokens=final.usage.input_tokens,
                completion_tokens=final.usage.output_tokens,
                total_tokens=final.usage.input_tokens + final.usage.output_tokens,
                latency_ms=latency_ms,
                time_to_first_token_ms=ttft_ms,
            )
        except (AnthropicAPIError, Exception) as exc:
            yield _error_result(self.provider, self.model, started, exc)


# ----------------------------------------------------------------------------
# OpenAI-compatible (OpenAI, DeepSeek, ...)
# ----------------------------------------------------------------------------


class _OpenAICompatibleClient(_BaseClient):
    def __init__(self) -> None:
        s = get_settings()
        self.provider = s.llm_provider.lower()
        self.model = s.resolved_model()
        self._client = AsyncOpenAI(
            api_key=s.resolved_api_key(),
            base_url=s.resolved_base_url(),
        )

    @staticmethod
    def _prepare(messages, system):
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend(messages)
        return msgs

    async def chat(self, messages, system, max_tokens):
        msgs = self._prepare(messages, system)
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

    async def chat_stream(self, messages, system, max_tokens):
        msgs = self._prepare(messages, system)
        started = time.perf_counter()
        ttft_ms: int | None = None
        chunks: list[str] = []
        prompt_tokens = completion_tokens = total_tokens = 0
        try:
            # stream_options.include_usage tells the API to emit a final usage
            # frame after the last content chunk. OpenAI + DeepSeek both honor it.
            stream = await self._client.chat.completions.create(
                model=self.model,
                messages=msgs,
                max_tokens=max_tokens,
                stream=True,
                stream_options={"include_usage": True},
            )
            async for chunk in stream:
                # Usage frames may have no choices, or choices with empty deltas.
                if chunk.choices:
                    delta = chunk.choices[0].delta
                    content = getattr(delta, "content", None)
                    if content:
                        if ttft_ms is None:
                            ttft_ms = int((time.perf_counter() - started) * 1000)
                        chunks.append(content)
                        yield content
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
                    completion_tokens = getattr(usage, "completion_tokens", 0) or 0
                    total_tokens = getattr(usage, "total_tokens", 0) or 0

            latency_ms = int((time.perf_counter() - started) * 1000)
            yield LLMResult(
                text="".join(chunks).strip(),
                provider=self.provider,
                model=self.model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                latency_ms=latency_ms,
                time_to_first_token_ms=ttft_ms,
            )
        except (OpenAIAPIError, Exception) as exc:
            yield _error_result(self.provider, self.model, started, exc)


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
        elif provider in ("openai", "deepseek", "gemini"):
            _singleton = _OpenAICompatibleClient()
        else:
            raise ValueError(f"unknown LLM_PROVIDER: {provider}")
    return _singleton
