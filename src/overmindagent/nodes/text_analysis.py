from __future__ import annotations

from typing import Protocol

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from overmindagent.graphs.state import TextAnalysisGraphState
from overmindagent.schemas.analysis import StructuredTextAnalysis, TextAnalysisOutput


class StructuredLLMFactory(Protocol):
    def create_structured_model(self, schema: type[BaseModel]):
        ...


class TextAnalysisNodes:
    def __init__(self, llm_factory: StructuredLLMFactory) -> None:
        self._llm_factory = llm_factory

    def preprocess(self, state: TextAnalysisGraphState) -> TextAnalysisGraphState:
        normalized_text = " ".join(state.get("text", "").split())
        return {"normalized_text": normalized_text}

    def route_after_preprocess(self, state: TextAnalysisGraphState) -> str:
        return "analyze" if state.get("normalized_text") else "empty"

    def analyze(self, state: TextAnalysisGraphState) -> TextAnalysisGraphState:
        model = self._llm_factory.create_structured_model(StructuredTextAnalysis)
        analysis = model.invoke(
            [
                SystemMessage(
                    content=(
                        "You are a precise text analysis engine. "
                        "Return a structured result that matches the requested schema."
                    )
                ),
                HumanMessage(
                    content=(
                        "Analyze the following text and extract language, summary, intent, "
                        f"sentiment, categories, and confidence:\n\n{state['normalized_text']}"
                    )
                ),
            ]
        )
        return {"analysis": StructuredTextAnalysis.model_validate(analysis)}

    def empty(self, state: TextAnalysisGraphState) -> TextAnalysisGraphState:
        return {
            "analysis": StructuredTextAnalysis(
                language="unknown",
                summary="No content provided.",
                intent="empty_input",
                sentiment="neutral",
                categories=["empty"],
                confidence=1.0,
            )
        }

    def finalize(self, state: TextAnalysisGraphState) -> TextAnalysisGraphState:
        return {
            "output": TextAnalysisOutput(
                normalized_text=state.get("normalized_text", ""),
                analysis=state["analysis"],
            )
        }
