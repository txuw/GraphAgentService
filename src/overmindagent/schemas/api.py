from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class GraphInvokeResponse(BaseModel):
    success: bool = Field(default=True)
    graph_name: str
    session_id: str | None = Field(default=None)
    data: dict[str, Any]


class GraphDescriptorResponse(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    stream_modes: list[str] = Field(default_factory=list)
