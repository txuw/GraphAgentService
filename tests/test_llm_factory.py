import asyncio

import pytest

from overmindagent.common.config import LLMSettings
from overmindagent.graphs import GraphRunContext
from overmindagent.llm import ChatModelBuildError, ChatModelFactory, LLMRouter
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
        return {
            "tools": list(tools),
            "kwargs": kwargs,
            "tags": self.tags,
            "metadata": self.metadata,
        }


def test_chat_model_factory_rejects_unknown_provider() -> None:
    factory = ChatModelFactory()

    with pytest.raises(ChatModelBuildError):
        factory.create(
            profile=type(
                "Profile",
                (),
                {"provider": "unknown"},
            )()
        )


def test_llm_router_resolves_alias_and_applies_observability_config() -> None:
    response = StructuredTextAnalysis(
        language="en",
        summary="summary",
        intent="classification",
        sentiment="neutral",
        categories=["test"],
        confidence=0.9,
    )
    factory = ChatModelFactory()
    factory.register("fake", lambda profile: FakeChatModel(response))
    router = LLMRouter(
        LLMSettings(
            default_profile="analysis",
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

    model = router.create_model(
        profile="structured_output",
        tags=("structured-output",),
        metadata={"purpose": "test"},
    )

    assert model.tags == ["structured-output"]
    assert model.metadata == {"purpose": "test"}


def test_graph_run_context_builds_structured_and_tool_models() -> None:
    response = StructuredTextAnalysis(
        language="en",
        summary="summary",
        intent="classification",
        sentiment="neutral",
        categories=["test"],
        confidence=0.9,
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
    context = GraphRunContext(
        llm_router=router,
        graph_name="text-analysis",
        llm_bindings={"analysis": "structured_output"},
    )

    structured_model = context.structured_model(
        binding="analysis",
        schema=StructuredTextAnalysis,
    )
    result = asyncio.run(structured_model.ainvoke([]))
    tool_model = context.tool_model(
        binding="analysis",
        tools=["lookup_weather"],
        tool_choice="auto",
    )

    assert result.intent == "classification"
    assert tool_model["tools"] == ["lookup_weather"]
    assert "graph:text-analysis" in tool_model["tags"]
    assert tool_model["metadata"]["binding"] == "analysis"
