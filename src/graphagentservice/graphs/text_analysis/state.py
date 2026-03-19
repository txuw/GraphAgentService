from __future__ import annotations

from typing import TypedDict

from graphagentservice.schemas.analysis import StructuredTextAnalysis


class TextAnalysisGraphInput(TypedDict):
    text: str


class TextAnalysisGraphState(TypedDict, total=False):
    text: str
    normalized_text: str
    analysis: StructuredTextAnalysis


class TextAnalysisGraphOutput(TypedDict):
    normalized_text: str
    analysis: StructuredTextAnalysis
