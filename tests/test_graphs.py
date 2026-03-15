import asyncio

from langchain_core.messages import AIMessage, ToolMessage

from overmindagent.common.checkpoint import DisabledCheckpointProvider
from overmindagent.common.config import LLMSettings
from overmindagent.graphs import GraphRunContext, create_graph_registry
from overmindagent.llm import ChatModelFactory, LLMRouter
from overmindagent.nodes import TextAnalysisNodes
from overmindagent.schemas.analysis import StructuredTextAnalysis


class FakeStructuredRunnable:
    def __init__(self, response) -> None:
        self._response = response

    async def ainvoke(self, messages):
        return self._response


class FakeChatModel:
    def __init__(self, response, *, tags=None, metadata=None) -> None:
        self._response = response
        self.tags = list(tags or [])
        self.metadata = dict(metadata or {})

    def with_config(self, *, tags=None, metadata=None):
        return FakeChatModel(
            self._response,
            tags=tags,
            metadata=metadata,
        )

    def with_structured_output(self, schema, method="json_schema", **kwargs):
        return FakeStructuredRunnable(self._response)

    def bind_tools(self, tools, **kwargs):
        return FakeToolRunnable(tags=self.tags, metadata=self.metadata)


class FakeToolRunnable:
    def __init__(self, *, tags=None, metadata=None) -> None:
        self.tags = list(tags or [])
        self.metadata = dict(metadata or {})

    async def ainvoke(self, messages):
        for message in reversed(messages):
            if isinstance(message, ToolMessage):
                return AIMessage(content=f"Final answer based on tool result: {message.content}")

        return AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "call_weather",
                    "name": "lookup_weather",
                    "args": {"location": "Shanghai"},
                    "type": "tool_call",
                }
            ],
        )


def test_text_analysis_nodes_handle_empty_input() -> None:
    nodes = TextAnalysisNodes()

    state = nodes.preprocess({"text": "   "})
    assert state["normalized_text"] == ""
    assert nodes.route_after_preprocess(state) == "empty"


def test_graph_registry_builds_text_analysis_graph() -> None:
    response = StructuredTextAnalysis(
        language="en",
        summary="Test summary",
        intent="classification",
        sentiment="neutral",
        categories=["test"],
        confidence=0.75,
    )
    factory = ChatModelFactory()
    factory.register("fake", lambda profile: FakeChatModel(response))
    router = LLMRouter(
        LLMSettings(
            aliases={"structured_output": "analysis"},
            profiles={
                "analysis": {
                    "provider": "fake",
                    "model": "fake-model",
                }
            },
        ),
        factory=factory,
    )

    registry = create_graph_registry(
        settings={"graphs": {"text-analysis": {"llm_bindings": {"analysis": "structured_output"}}}},
        checkpoint_provider=DisabledCheckpointProvider(),
    )
    runtime = registry.get("text-analysis")
    result = asyncio.run(
        runtime.graph.ainvoke(
            {"text": "hello world"},
            context=GraphRunContext(
                llm_router=router,
                graph_name=runtime.name,
                llm_bindings=runtime.llm_bindings,
            ),
        )
    )

    assert set(registry.list_names()) == {"text-analysis", "tool-agent"}
    assert runtime.stream_modes == ("updates", "messages", "values")
    assert result["analysis"].intent == "classification"


def test_tool_agent_graph_executes_tool_node() -> None:
    response = StructuredTextAnalysis(
        language="en",
        summary="Test summary",
        intent="classification",
        sentiment="neutral",
        categories=["test"],
        confidence=0.75,
    )
    factory = ChatModelFactory()
    factory.register("fake", lambda profile: FakeChatModel(response))
    router = LLMRouter(
        LLMSettings(
            aliases={
                "structured_output": "analysis",
                "tool_calling": "analysis",
            },
            profiles={
                "analysis": {
                    "provider": "fake",
                    "model": "fake-model",
                }
            },
        ),
        factory=factory,
    )

    registry = create_graph_registry(
        settings={
            "graphs": {
                "tool-agent": {
                    "llm_bindings": {
                        "agent": "tool_calling",
                    }
                }
            }
        },
        checkpoint_provider=DisabledCheckpointProvider(),
    )
    runtime = registry.get("tool-agent")
    result = asyncio.run(
        runtime.graph.ainvoke(
            {"query": "What is the weather in Shanghai?"},
            context=GraphRunContext(
                llm_router=router,
                graph_name=runtime.name,
                llm_bindings=runtime.llm_bindings,
            ),
        )
    )

    assert runtime.stream_modes == ("updates", "messages", "values")
    assert result["answer"].startswith("Final answer based on tool result:")
    assert result["tools_used"][0].tool_name == "lookup_weather"
