from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class LLMEventType(StrEnum):
    TEXT_DELTA = "text_delta"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    COMPLETED = "completed"
    ERROR = "error"


class LLMMessage(BaseModel):
    role: str
    content: str


class ToolSpec(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    call_id: str
    output: str
    is_error: bool = False


class LLMRequest(BaseModel):
    system_prompt: str | None = None
    messages: list[LLMMessage] = Field(default_factory=list)
    response_schema: type[BaseModel] | None = None
    tools: list[ToolSpec] = Field(default_factory=list)
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    provider_options: dict[str, Any] = Field(default_factory=dict)


class LLMResponse(BaseModel):
    text: str = ""
    structured: BaseModel | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)
    finish_reason: str | None = None
    provider_name: str
    model: str
    raw: Any = None
    extensions: dict[str, Any] = Field(default_factory=dict)


class LLMEvent(BaseModel):
    type: LLMEventType
    text_delta: str | None = None
    tool_call: ToolCall | None = None
    tool_result: ToolResult | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    raw: Any = None
    extensions: dict[str, Any] = Field(default_factory=dict)
