from __future__ import annotations

from typing import Any

from overmindagent.common.checkpoint import CheckpointProvider
from overmindagent.llm.factory import LLMModelFactory
from overmindagent.nodes.text_analysis import TextAnalysisNodes

from .text_analysis import TextAnalysisGraphBuilder


class GraphNotFoundError(KeyError):
    pass


class GraphRegistry:
    def __init__(self, graphs: dict[str, Any]) -> None:
        self._graphs = graphs

    def get(self, graph_name: str):
        try:
            return self._graphs[graph_name]
        except KeyError as exc:
            raise GraphNotFoundError(f"Unknown graph: {graph_name}") from exc

    def list_names(self) -> tuple[str, ...]:
        return tuple(self._graphs.keys())


def create_graph_registry(
    llm_factory: LLMModelFactory,
    checkpoint_provider: CheckpointProvider,
) -> GraphRegistry:
    checkpointer = checkpoint_provider.build()
    text_analysis_graph = TextAnalysisGraphBuilder(
        nodes=TextAnalysisNodes(llm_factory=llm_factory),
        checkpointer=checkpointer,
    ).build()

    return GraphRegistry({TextAnalysisGraphBuilder.name: text_analysis_graph})
