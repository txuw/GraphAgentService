from __future__ import annotations

import logging
import time
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from graphagentservice.common.logging import context_extra
from graphagentservice.graphs.runtime import GraphRunContext

from .state import ImageAnalyzeCaloriesGraphState
from .prompts import ANALYSIS_PROMPTS, ANALYZE_SYSTEM_PROMPT
from graphagentservice.schemas.image_calories import CalorieInfo

_logger = logging.getLogger(__name__)


class ImageAnalyzeCaloriesAgentNodes:
    def __init__(self, llm_binding: str = "analysis") -> None:
        self._llm_binding = llm_binding

    async def analyze(
            self,
            state: ImageAnalyzeCaloriesGraphState,
            runtime: Runtime[GraphRunContext],
    ) -> ImageAnalyzeCaloriesGraphState:
        started = time.perf_counter()
        _logger.info(
            "Image calorie analyze node started",
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
            model = runtime.context.structured_model_with_json_object(
                schema=CalorieInfo,
                binding=self._llm_binding,
                tags=("multimodal",),
            )
            response = await model.ainvoke(self.build_messages(
                prompt=state.get("text", "").strip() or ANALYSIS_PROMPTS,
                image_url=state["image_url"].strip(),
            ))
            result = {"answer": response}
        except Exception as exc:
            _logger.exception(
                "Image calorie analyze node failed",
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
            "Image calorie analyze node completed",
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

    @staticmethod
    def build_messages(*, prompt: str, image_url: str) -> list[object]:
        return [
            SystemMessage(content=ANALYZE_SYSTEM_PROMPT),
            HumanMessage(
                content=[
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ]
            ),
        ]
