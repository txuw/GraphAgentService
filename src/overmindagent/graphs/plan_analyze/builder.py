from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langgraph.graph import END, START, StateGraph

from overmindagent.graphs.runtime import GraphRunContext, GraphRuntime
from overmindagent.schemas.plan_analyze import PlanAnalyzeOutput, PlanAnalyzeRequest

from .nodes import PlanAnalyzeNodes
from .state import (
    PlanAnalyzeGraphInput,
    PlanAnalyzeGraphOutput,
    PlanAnalyzeGraphState,
)


class PlanAnalyzeGraphBuilder:
    name = "plan-analyze"
    description = "Workflow graph that drafts a plan first and then produces an analysis."

    def __init__(
        self,
        graph_settings: Mapping[str, Any] | None = None,
        checkpointer: Any | None = None,
    ) -> None:
        self._graph_settings = graph_settings or {}
        self._nodes = PlanAnalyzeNodes()
        self._checkpointer = checkpointer

    def build(self) -> GraphRuntime:
        graph = StateGraph(
            state_schema=PlanAnalyzeGraphState,
            context_schema=GraphRunContext,
            input_schema=PlanAnalyzeGraphInput,
            output_schema=PlanAnalyzeGraphOutput,
        )
        graph.add_node("prepare", self._nodes.prepare)
        graph.add_node("plan", self._nodes.plan)
        graph.add_node("analyze", self._nodes.analyze)
        graph.add_node("empty", self._nodes.empty)
        graph.add_node("finalize", self._nodes.finalize)

        graph.add_edge(START, "prepare")
        graph.add_conditional_edges(
            "prepare",
            self._nodes.route_after_prepare,
            {
                "plan": "plan",
                "empty": "empty",
            },
        )
        graph.add_edge("plan", "analyze")
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
            input_model=PlanAnalyzeRequest,
            output_model=PlanAnalyzeOutput,
            llm_bindings=self._llm_bindings(),
            stream_modes=("updates", "messages", "values"),
        )

    def _llm_bindings(self) -> dict[str, str]:
        configured_bindings = self._graph_settings.get("llm_bindings", {})
        if hasattr(configured_bindings, "items"):
            return {
                str(binding_name): str(profile_name)
                for binding_name, profile_name in configured_bindings.items()
            } or {
                "planner": "planning",
                "analysis": "structured_output",
            }
        return {
            "planner": "planning",
            "analysis": "structured_output",
        }
