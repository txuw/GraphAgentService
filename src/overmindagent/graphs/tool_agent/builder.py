from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import tools_condition

from overmindagent.graphs.runtime import GraphRunContext, GraphRuntime
from overmindagent.graphs.tool_agent.nodes import ToolAgentNodes
from overmindagent.graphs.tool_agent.state import (
    ToolAgentGraphInput,
    ToolAgentGraphOutput,
    ToolAgentGraphState,
)
from overmindagent.schemas.tool_agent import ToolAgentOutput, ToolAgentRequest


class ToolAgentGraphBuilder:
    name = "tool-agent"
    description = "Agent-style graph that uses LangGraph ToolNode for iterative tool calling."

    def __init__(
        self,
        graph_settings: Mapping[str, Any] | None = None,
        checkpointer: Any | None = None,
        tools: Sequence[BaseTool] | None = None,
    ) -> None:
        self._graph_settings = graph_settings or {}
        self._nodes = ToolAgentNodes(tools=tools)
        self._checkpointer = checkpointer

    def build(self) -> GraphRuntime:
        graph = StateGraph(
            state_schema=ToolAgentGraphState,
            context_schema=GraphRunContext,
            input_schema=ToolAgentGraphInput,
            output_schema=ToolAgentGraphOutput,
        )
        graph.add_node("prepare", self._nodes.prepare)
        graph.add_node("agent", self._nodes.agent)
        graph.add_node("tools", self._nodes.tools)
        graph.add_node("empty", self._nodes.empty)
        graph.add_node("finalize", self._nodes.finalize)

        graph.add_edge(START, "prepare")
        graph.add_conditional_edges(
            "prepare",
            self._nodes.route_after_prepare,
            {
                "agent": "agent",
                "empty": "empty",
            },
        )
        graph.add_conditional_edges(
            "agent",
            tools_condition,
            {
                "tools": "tools",
                "__end__": "finalize",
            },
        )
        graph.add_edge("tools", "agent")
        graph.add_edge("empty", "finalize")
        graph.add_edge("finalize", END)

        compile_kwargs: dict[str, Any] = {}
        if self._checkpointer is not None:
            compile_kwargs["checkpointer"] = self._checkpointer

        return GraphRuntime(
            name=self.name,
            description=self.description,
            graph=graph.compile(**compile_kwargs),
            input_model=ToolAgentRequest,
            output_model=ToolAgentOutput,
            llm_bindings=self._llm_bindings(),
            stream_modes=("updates", "messages", "values"),
        )

    def _llm_bindings(self) -> dict[str, str]:
        configured_bindings = self._graph_settings.get("llm_bindings", {})
        if hasattr(configured_bindings, "items"):
            return {
                str(binding_name): str(profile_name)
                for binding_name, profile_name in configured_bindings.items()
            } or {"agent": "tool_calling"}
        return {"agent": "tool_calling"}
