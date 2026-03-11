from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TextAnalysisRequest(BaseModel):
    text: str = Field(default="")
    session_id: str | None = Field(default=None)


class StructuredTextAnalysis(BaseModel):
    language: str = Field(default="unknown")
    summary: str = Field(default="")
    intent: str = Field(default="unknown")
    sentiment: Literal["positive", "neutral", "negative"] = Field(default="neutral")
    categories: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class TextAnalysisOutput(BaseModel):
    normalized_text: str = Field(default="")
    analysis: StructuredTextAnalysis
