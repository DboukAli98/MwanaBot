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


class ToolAction(BaseModel):
    type: str = "navigate"
    label: str
    route: str
    params: dict[str, str] = Field(default_factory=dict)
    severity: str = "default"


class ToolResultPayload(BaseModel):
    name: str
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)
    actions: list[ToolAction] = Field(default_factory=list)


class ChatResponse(BaseModel):
    answer: str
    conversation_id: str | None = None
    sources: list[Source] = Field(default_factory=list)
    # Structured tool outputs the mobile client renders as cards. Each
    # entry corresponds to one tool the LLM decided to invoke for this
    # turn (typically 0-2 entries). Optional: empty when no tool fired.
    tool_results: list[ToolResultPayload] = Field(default_factory=list)


class IngestTextRequest(BaseModel):
    texts: list[str] = Field(..., min_length=1)
    namespace: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class IngestResponse(BaseModel):
    added: int
    namespace: str | None = None

