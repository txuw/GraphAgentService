from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Protocol

from overmindagent.graphs.state import TextAnalysisGraphState
from overmindagent.llm import LLMEvent, LLMMessage, LLMRequest
from overmindagent.schemas.analysis import StructuredTextAnalysis, TextAnalysisOutput


class LLMSessionProtocol(Protocol):
    async def invoke(self, request: LLMRequest):
        ...

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMEvent]:
        ...


class TextAnalysisNodes:
    def __init__(self, llm_session: LLMSessionProtocol) -> None:
        self._llm_session = llm_session

    def preprocess(self, state: TextAnalysisGraphState) -> TextAnalysisGraphState:
        normalized_text = " ".join(state.get("text", "").split())
        return {"normalized_text": normalized_text}

    def route_after_preprocess(self, state: TextAnalysisGraphState) -> str:
        return "analyze" if state.get("normalized_text") else "empty"

    async def analyze(self, state: TextAnalysisGraphState) -> TextAnalysisGraphState:
        response = await self._llm_session.invoke(self.build_request(state["normalized_text"]))
        analysis = StructuredTextAnalysis.model_validate(response.structured)
        return {"analysis": analysis}

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

    def build_request(self, normalized_text: str, *, stream: bool = False) -> LLMRequest:
        return LLMRequest(
            system_prompt=(
                "You are a precise text analysis engine. "
                "Return a structured result that matches the requested schema."
            ),
            messages=[
                LLMMessage(
                    role="user",
                    content=(
                        "Analyze the following text and extract language, summary, intent, "
                        f"sentiment, categories, and confidence:\n\n{normalized_text}"
                    ),
                )
            ],
            response_schema=StructuredTextAnalysis,
            stream=stream,
        )

    async def stream_analysis(self, normalized_text: str) -> AsyncIterator[LLMEvent]:
        async for event in self._llm_session.stream(self.build_request(normalized_text, stream=True)):
            yield event

    def parse_analysis_text(self, payload_text: str) -> StructuredTextAnalysis:
        return StructuredTextAnalysis.model_validate(json.loads(payload_text))
