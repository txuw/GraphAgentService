from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from overmindagent.common.checkpoint import CheckpointProvider
from overmindagent.llm.factory import LLMSessionFactory
from overmindagent.nodes.text_analysis import TextAnalysisNodes

from .text_analysis import TextAnalysisGraphBuilder


class GraphNotFoundError(KeyError):
    pass


@dataclass(frozen=True, slots=True)
class GraphRuntime:
    graph: Any
    nodes: TextAnalysisNodes


class GraphRegistry:
    def __init__(self, graphs: dict[str, GraphRuntime]) -> None:
        self._graphs = graphs

    def get(self, graph_name: str) -> GraphRuntime:
        try:
            return self._graphs[graph_name]
        except KeyError as exc:
            raise GraphNotFoundError(f"Unknown graph: {graph_name}") from exc

    def list_names(self) -> tuple[str, ...]:
        return tuple(self._graphs.keys())


def create_graph_registry(
    llm_factory: LLMSessionFactory,
    checkpoint_provider: CheckpointProvider,
) -> GraphRegistry:
    checkpointer = checkpoint_provider.build()
    text_analysis_nodes = TextAnalysisNodes(llm_session=llm_factory.create())
    text_analysis_graph = TextAnalysisGraphBuilder(
        nodes=text_analysis_nodes,
        checkpointer=checkpointer,
    ).build()

    return GraphRegistry(
        {
            TextAnalysisGraphBuilder.name: GraphRuntime(
                graph=text_analysis_graph,
                nodes=text_analysis_nodes,
            )
        }
    )
