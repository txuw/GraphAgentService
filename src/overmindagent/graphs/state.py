from __future__ import annotations

from typing import TypedDict

from overmindagent.schemas.analysis import StructuredTextAnalysis, TextAnalysisOutput


class TextAnalysisGraphState(TypedDict, total=False):
    text: str
    normalized_text: str
    analysis: StructuredTextAnalysis
    output: TextAnalysisOutput
