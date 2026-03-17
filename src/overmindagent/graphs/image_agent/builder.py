from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langgraph.graph import END, START, StateGraph

from overmindagent.graphs.runtime import GraphRunContext, GraphRuntime
from overmindagent.schemas.image import ImageAgentOutput, ImageAgentRequest

from .nodes import ImageAgentNodes
from .state import ImageGraphInput, ImageGraphOutput, ImageGraphState


class ImageGraphBuilder:
    name = "image-agent"
    description = "Multimodal graph that answers a user question about an image URL."

    def __init__(
        self,
        graph_settings: Mapping[str, Any] | None = None,
        checkpointer: Any | None = None,
    ) -> None:
        self._graph_settings = graph_settings or {}
        self._nodes = ImageAgentNodes()
        self._checkpointer = checkpointer

    def build(self) -> GraphRuntime:
        graph = StateGraph(
            state_schema=ImageGraphState,
            context_schema=GraphRunContext,
            input_schema=ImageGraphInput,
            output_schema=ImageGraphOutput,
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
            input_model=ImageAgentRequest,
            output_model=ImageAgentOutput,
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
