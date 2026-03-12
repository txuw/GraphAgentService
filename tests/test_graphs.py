import asyncio
from collections.abc import AsyncIterator

from overmindagent.common.checkpoint import DisabledCheckpointProvider
from overmindagent.graphs import create_graph_registry
from overmindagent.llm import LLMEvent, LLMEventType, LLMRequest, LLMResponse
from overmindagent.nodes import TextAnalysisNodes
from overmindagent.schemas.analysis import StructuredTextAnalysis


class FakeLLMSession:
    async def invoke(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            text='{"language":"en","summary":"Test summary","intent":"classification","sentiment":"neutral","categories":["test"],"confidence":0.75}',
            structured=StructuredTextAnalysis(
                language="en",
                summary="Test summary",
                intent="classification",
                sentiment="neutral",
                categories=["test"],
                confidence=0.75,
            ),
            provider_name="fake",
            model="fake-model",
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMEvent]:
        yield LLMEvent(
            type=LLMEventType.TEXT_DELTA,
            text_delta='{"language":"en","summary":"Test summary","intent":"classification","sentiment":"neutral","categories":["test"],"confidence":0.75}',
        )
        yield LLMEvent(type=LLMEventType.COMPLETED)


def test_text_analysis_nodes_handle_empty_input() -> None:
    nodes = TextAnalysisNodes(llm_session=FakeLLMSession())

    state = nodes.preprocess({"text": "   "})
    assert state["normalized_text"] == ""
    assert nodes.route_after_preprocess(state) == "empty"


def test_graph_registry_builds_text_analysis_graph() -> None:
    from pydantic import SecretStr

    from overmindagent.common.config import LLMSettings
    from overmindagent.llm.factory import LLMSessionFactory

    factory = LLMSessionFactory(
        LLMSettings(
            api_key=SecretStr("test-key"),
            provider="openai",
            protocol="responses",
        )
    )
    factory._adapters[("openai", "responses")] = type(
        "FakeAdapter",
        (),
        {
            "provider_name": "openai",
            "protocol_name": "responses",
            "build_session": staticmethod(lambda settings, tool_registry=None: FakeLLMSession()),
        },
    )()

    registry = create_graph_registry(
        llm_factory=factory,
        checkpoint_provider=DisabledCheckpointProvider(),
    )

    result = asyncio.run(registry.get("text-analysis").graph.ainvoke({"text": "hello world"}))

    assert registry.list_names() == ("text-analysis",)
    assert result["output"].analysis.intent == "classification"
