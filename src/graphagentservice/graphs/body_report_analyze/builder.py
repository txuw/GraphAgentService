from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langgraph.graph import END, START, StateGraph

from graphagentservice.graphs.runtime import GraphRunContext, GraphRuntime
from graphagentservice.schemas.body_report import BodyReportOutput, BodyReportRequest

from .nodes import BodyReportAnalyzeNodes
from .state import (
    BodyReportAnalyzeGraphInput,
    BodyReportAnalyzeGraphOutput,
    BodyReportAnalyzeGraphState,
)


class BodyReportAnalyzeGraphBuilder:
    name = "body-report-analyze"
    description = "Multimodal graph that extracts structured body report metrics from an image URL."

    def __init__(
        self,
        graph_settings: Mapping[str, Any] | None = None,
        checkpointer: Any | None = None,
    ) -> None:
        self._graph_settings = graph_settings or {}
        self._nodes = BodyReportAnalyzeNodes()
        self._checkpointer = checkpointer

    def build(self) -> GraphRuntime:
        graph = StateGraph(
            state_schema=BodyReportAnalyzeGraphState,
            context_schema=GraphRunContext,
            input_schema=BodyReportAnalyzeGraphInput,
            output_schema=BodyReportAnalyzeGraphOutput,
        )
        graph.add_node("analyze", self._nodes.analyze)

        graph.add_edge(START, "analyze")
        graph.add_edge("analyze", END)

        compile_kwargs: dict[str, Any] = {}
        if self._checkpointer is not None:
            compile_kwargs["checkpointer"] = self._checkpointer

        return GraphRuntime(
            name=self.name,
            description=self.description,
            graph=graph.compile(**compile_kwargs),
            input_model=BodyReportRequest,
            output_model=BodyReportOutput,
            llm_bindings=self._llm_bindings(),
            stream_modes=("updates", "messages", "values"),
        )

    def _llm_bindings(self) -> dict[str, str]:
        configured_bindings = self._graph_settings.get("llm_bindings", {})
        if hasattr(configured_bindings, "items"):
            return {
                str(binding_name): str(profile_name)
                for binding_name, profile_name in configured_bindings.items()
            } or {"analysis": "multimodal"}
        return {"analysis": "multimodal"}
