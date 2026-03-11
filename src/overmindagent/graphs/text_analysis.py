from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from overmindagent.graphs.state import TextAnalysisGraphState
from overmindagent.nodes.text_analysis import TextAnalysisNodes


class TextAnalysisGraphBuilder:
    name = "text-analysis"

    def __init__(
        self,
        nodes: TextAnalysisNodes,
        checkpointer: Any | None = None,
    ) -> None:
        self._nodes = nodes
        self._checkpointer = checkpointer

    def build(self):
        graph = StateGraph(TextAnalysisGraphState)
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

        return graph.compile(**compile_kwargs)
