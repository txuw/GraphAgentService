from __future__ import annotations

from pydantic import BaseModel, Field

from .analysis import TextAnalysisOutput


class GraphInvokeResponse(BaseModel):
    success: bool = Field(default=True)
    graph_name: str
    session_id: str | None = Field(default=None)
    data: TextAnalysisOutput
