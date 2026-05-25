from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: str | None = Field(default=None, max_length=64)
    message: str = Field(min_length=1, max_length=20_000)
    # Override the server-default LLM_PROVIDER for this single request.
    provider: str | None = Field(default=None, max_length=32)


class ChatResponse(BaseModel):
    session_id: str
    response: str


class MessageOut(BaseModel):
    role: str
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}


class SessionSummary(BaseModel):
    id: str
    title: str | None
    created_at: datetime
    last_active_at: datetime

    model_config = {"from_attributes": True}


class SessionStats(BaseModel):
    session_id: str
    message_count: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    avg_latency_ms: float
    total_cost: float
    error_count: int
    last_seen_at: datetime

    model_config = {"from_attributes": True}


class OverallStats(BaseModel):
    sessions: int
    total_messages: int
    total_tokens: int
    avg_latency_ms: float
    total_cost: float
    error_count: int


class StatsResponse(BaseModel):
    overall: OverallStats
    per_session: list[SessionStats]


class HealthResponse(BaseModel):
    status: str
    db: str


class ProviderInfo(BaseModel):
    name: str
    available: bool
    model: str
    is_default: bool


class ProvidersResponse(BaseModel):
    default: str
    providers: list[ProviderInfo]


class BucketPoint(BaseModel):
    bucket_ts: datetime
    message_count: int
    total_tokens: int
    avg_latency_ms: float
    p95_latency_ms: float
    error_count: int
    total_cost: float


class TimeseriesResponse(BaseModel):
    points: list[BucketPoint]
