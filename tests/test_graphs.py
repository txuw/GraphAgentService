from pydantic import BaseModel

from overmindagent.common.checkpoint import DisabledCheckpointProvider
from overmindagent.graphs import create_graph_registry
from overmindagent.nodes import TextAnalysisNodes
from overmindagent.schemas.analysis import StructuredTextAnalysis


class FakeStructuredRunnable:
    def invoke(self, _messages):
        return StructuredTextAnalysis(
            language="en",
            summary="Test summary",
            intent="classification",
            sentiment="neutral",
            categories=["test"],
            confidence=0.75,
        )


class FakeLLMFactory:
    def create_structured_model(self, schema: type[BaseModel]):
        return FakeStructuredRunnable()


def test_text_analysis_nodes_handle_empty_input() -> None:
    nodes = TextAnalysisNodes(llm_factory=FakeLLMFactory())

    state = nodes.preprocess({"text": "   "})
    assert state["normalized_text"] == ""
    assert nodes.route_after_preprocess(state) == "empty"


def test_graph_registry_builds_text_analysis_graph() -> None:
    registry = create_graph_registry(
        llm_factory=FakeLLMFactory(),
        checkpoint_provider=DisabledCheckpointProvider(),
    )

    result = registry.get("text-analysis").invoke({"text": "hello world"})

    assert registry.list_names() == ("text-analysis",)
    assert result["output"].analysis.intent == "classification"
