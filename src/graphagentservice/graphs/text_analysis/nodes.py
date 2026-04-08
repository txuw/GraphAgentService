from __future__ import annotations

import logging
import time

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from graphagentservice.common.logging import context_extra
from graphagentservice.graphs.runtime import GraphRunContext
from graphagentservice.schemas.analysis import StructuredTextAnalysis

from .prompts import ANALYSIS_PROMPT_TEMPLATE, SYSTEM_PROMPT
from .state import TextAnalysisGraphState

_logger = logging.getLogger(__name__)


class TextAnalysisNodes:
    def __init__(self, llm_binding: str = "analysis") -> None:
        self._llm_binding = llm_binding

    def preprocess(self, state: TextAnalysisGraphState) -> TextAnalysisGraphState:
        normalized_text = " ".join(state.get("text", "").split())
        result = {"normalized_text": normalized_text}
        _logger.info(
            "Text analysis preprocess completed",
            extra=context_extra(
                event="graph_node_completed",
                graph="text-analysis",
                node="preprocess",
                status="completed",
            ),
        )
        return result

    def route_after_preprocess(self, state: TextAnalysisGraphState) -> str:
        return "analyze" if state.get("normalized_text") else "empty"

    async def analyze(
        self,
        state: TextAnalysisGraphState,
        runtime: Runtime[GraphRunContext],
    ) -> TextAnalysisGraphState:
        started = time.perf_counter()
        _logger.info(
            "Text analysis node started",
            extra=context_extra(
                event="graph_node_started",
                graph=runtime.context.graph_name,
                sessionId=runtime.context.session_id,
                requestId=runtime.context.request_id,
                pageId=runtime.context.page_id,
                node="analyze",
                status="started",
            ),
        )
        try:
            model = runtime.context.structured_model(
                binding=self._llm_binding,
                schema=StructuredTextAnalysis,
                tags=("structured-output",),
            )
            analysis = await model.invoke(self.build_messages(state["normalized_text"]))
            result = {"analysis": analysis}
        except Exception as exc:
            _logger.exception(
                "Text analysis node failed",
                extra=context_extra(
                    event="graph_node_failed",
                    graph=runtime.context.graph_name,
                    sessionId=runtime.context.session_id,
                    requestId=runtime.context.request_id,
                    pageId=runtime.context.page_id,
                    node="analyze",
                    status="failed",
                    elapsedMs=round((time.perf_counter() - started) * 1000),
                    errorType=type(exc).__name__,
                ),
            )
            raise
        _logger.info(
            "Text analysis node completed",
            extra=context_extra(
                event="graph_node_completed",
                graph=runtime.context.graph_name,
                sessionId=runtime.context.session_id,
                requestId=runtime.context.request_id,
                pageId=runtime.context.page_id,
                node="analyze",
                status="completed",
                elapsedMs=round((time.perf_counter() - started) * 1000),
            ),
        )
        return result

    def empty(self, state: TextAnalysisGraphState) -> TextAnalysisGraphState:
        result = {
            "analysis": StructuredTextAnalysis(
                language="unknown",
                summary="No content provided.",
                intent="empty_input",
                sentiment="neutral",
                categories=["empty"],
                confidence=1.0,
            )
        }
        _logger.info(
            "Text analysis empty completed",
            extra=context_extra(
                event="graph_node_completed",
                graph="text-analysis",
                node="empty",
                status="completed",
            ),
        )
        return result

    def finalize(self, state: TextAnalysisGraphState) -> TextAnalysisGraphState:
        result = {
            "normalized_text": state.get("normalized_text", ""),
            "analysis": state["analysis"],
        }
        _logger.info(
            "Text analysis finalize completed",
            extra=context_extra(
                event="graph_node_completed",
                graph="text-analysis",
                node="finalize",
                status="completed",
            ),
        )
        return result

    @staticmethod
    def build_messages(normalized_text: str) -> list[object]:
        return [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=ANALYSIS_PROMPT_TEMPLATE.format(text=normalized_text)),
        ]
