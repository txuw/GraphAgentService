from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ToolAgentRequest(BaseModel):
    query: str = Field(default="")


class ToolCallTrace(BaseModel):
    tool_name: str = Field(default="")
    tool_args: dict[str, Any] = Field(default_factory=dict)
    result: str = Field(default="")


class ToolAgentOutput(BaseModel):
    answer: str = Field(default="")
    tools_used: list[ToolCallTrace] = Field(default_factory=list)
