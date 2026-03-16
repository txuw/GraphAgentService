from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langgraph.graph import END, START, StateGraph

from overmindagent.graphs.runtime import GraphRunContext, GraphRuntime
from overmindagent.schemas.analysis import TextAnalysisOutput, TextAnalysisRequest

from .nodes import TextAnalysisNodes
from .state import (
    TextAnalysisGraphInput,
    TextAnalysisGraphOutput,
    TextAnalysisGraphState,
)


class TextAnalysisGraphBuilder:
    name = "text-analysis"
    description = "Workflow graph for deterministic text normalization and structured analysis."

    def __init__(
        self,
        graph_settings: Mapping[str, Any] | None = None,
        checkpointer: Any | None = None,
    ) -> None:
        self._graph_settings = graph_settings or {}
        self._nodes = TextAnalysisNodes()
        self._checkpointer = checkpointer

    def build(self) -> GraphRuntime:
        graph = StateGraph(
            state_schema=TextAnalysisGraphState,
            context_schema=GraphRunContext,
            input_schema=TextAnalysisGraphInput,
            output_schema=TextAnalysisGraphOutput,
        )
        graph.add_node("preprocess", self._nodes.preprocess)
        graph.add_node("analyze", self._nodes.analyze)
        graph.add_node("empty", self._nodes.empty)
        graph.add_node("finalize", self._nodes.finalize)

        graph.add_edge(START, "preprocess")
        graph.add_conditional_edges(
            "preprocess",
            self._nodes.route_after_preprocess,
            {
                "analyze": "analyze",
                "empty": "empty",
            },
        )
        graph.add_edge("analyze", "finalize")
        graph.add_edge("empty", "finalize")
        graph.add_edge("finalize", END)

        compile_kwargs: dict[str, Any] = {}
        if self._checkpointer is not None:
            compile_kwargs["checkpointer"] = self._checkpointer

        return GraphRuntime(
            name=self.name,
            description=self.description,
            graph=graph.compile(**compile_kwargs),
            input_model=TextAnalysisRequest,
            output_model=TextAnalysisOutput,
            llm_bindings=self._llm_bindings(),
            stream_modes=("updates", "messages", "values"),
        )

    def _llm_bindings(self) -> dict[str, str]:
        configured_bindings = self._graph_settings.get("llm_bindings", {})
        if hasattr(configured_bindings, "items"):
            return {
                str(binding_name): str(profile_name)
                for binding_name, profile_name in configured_bindings.items()
            } or {"analysis": "structured_output"}
        return {"analysis": "structured_output"}
