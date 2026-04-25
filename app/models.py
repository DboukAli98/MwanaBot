from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    user_id: str | None = None
    conversation_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Source(BaseModel):
    title: str | None = None
    content: str
    score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    answer: str
    conversation_id: str | None = None
    sources: list[Source] = Field(default_factory=list)


class IngestTextRequest(BaseModel):
    texts: list[str] = Field(..., min_length=1)
    namespace: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class IngestResponse(BaseModel):
    added: int
    namespace: str | None = None

