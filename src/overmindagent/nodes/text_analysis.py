from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from overmindagent.graphs.runtime import GraphRunContext
from overmindagent.graphs.state import TextAnalysisGraphState
from overmindagent.schemas.analysis import StructuredTextAnalysis


class TextAnalysisNodes:
    def __init__(self, llm_binding: str = "analysis") -> None:
        self._llm_binding = llm_binding

    def preprocess(self, state: TextAnalysisGraphState) -> TextAnalysisGraphState:
        normalized_text = " ".join(state.get("text", "").split())
        return {"normalized_text": normalized_text}

    def route_after_preprocess(self, state: TextAnalysisGraphState) -> str:
        return "analyze" if state.get("normalized_text") else "empty"

    async def analyze(
        self,
        state: TextAnalysisGraphState,
        runtime: Runtime[GraphRunContext],
    ) -> TextAnalysisGraphState:
        model = runtime.context.structured_model(
            binding=self._llm_binding,
            schema=StructuredTextAnalysis,
            tags=("structured-output",),
        )
        analysis = await model.ainvoke(self.build_messages(state["normalized_text"]))
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
            "normalized_text": state.get("normalized_text", ""),
            "analysis": state["analysis"],
        }

    @staticmethod
    def build_messages(normalized_text: str) -> list[object]:
        return [
            SystemMessage(
                content=(
                    "You are a precise text analysis engine. "
                    "Return a structured result that matches the requested schema."
                )
            ),
            HumanMessage(
                content=(
                    "Analyze the following text and extract language, summary, intent, "
                    f"sentiment, categories, and confidence:\n\n{normalized_text}"
                )
            ),
        ]
