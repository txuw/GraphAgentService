from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from overmindagent.graphs.registry import GraphRegistry
from overmindagent.schemas.analysis import TextAnalysisOutput, TextAnalysisRequest


@dataclass(slots=True)
class GraphInvocationResult:
    graph_name: str
    session_id: str
    output: TextAnalysisOutput


class GraphService:
    def __init__(self, registry: GraphRegistry) -> None:
        self._registry = registry

    def invoke(
        self,
        graph_name: str,
        payload: TextAnalysisRequest,
    ) -> GraphInvocationResult:
        graph = self._registry.get(graph_name)
        session_id = payload.session_id or uuid4().hex
        state = graph.invoke(
            {"text": payload.text},
            config={"configurable": {"thread_id": session_id}},
        )
        return GraphInvocationResult(
            graph_name=graph_name,
            session_id=session_id,
            output=TextAnalysisOutput.model_validate(state["output"]),
        )
