from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from overmindagent.common.auth import AuthenticatedUser
from overmindagent.graphs.plan_analyze import PlanAnalyzeGraphBuilder
from overmindagent.graphs.registry import GraphRegistry
from overmindagent.main import create_app
from overmindagent.services.graph_service import (
    GraphPayloadValidationError,
    GraphRequestContext,
    GraphService,
)


class FakeChatModel:
    def __init__(self, responses: dict[str, str] | None = None) -> None:
        self._responses = responses or {}
        self.calls: list[dict[str, object]] = []
        self.tags: list[str] = []
        self.metadata: dict[str, object] = {}
        self.bound_tool_names: list[str] = []

    def with_config(
        self,
        *,
        tags: list[str] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> "FakeChatModel":
        self.tags = list(tags or [])
        self.metadata = dict(metadata or {})
        return self

    def bind_tools(self, tools, **kwargs) -> "FakeChatModel":
        self.bound_tool_names = [tool.name for tool in tools]
        return self

    async def ainvoke(self, messages: list[object]) -> AIMessage:
        self.calls.append(
            {
                "messages": messages,
                "tags": list(self.tags),
                "metadata": dict(self.metadata),
            }
        )
        binding = str(self.metadata.get("binding", ""))
        return AIMessage(content=self._responses.get(binding, f"default:{binding}"))


class FakeLLMRouter:
    def __init__(self, model: FakeChatModel) -> None:
        self._model = model

    @staticmethod
    def resolve_profile(profile: str | None = None) -> SimpleNamespace:
        return SimpleNamespace(name=profile or "default")

    def create_model(
        self,
        *,
        profile: str | None = None,
        tags: tuple[str, ...] = (),
        metadata: dict[str, object] | None = None,
    ) -> FakeChatModel:
        return self._model.with_config(tags=list(tags), metadata=dict(metadata or {}))


class FakeAuthenticator:
    def authenticate_request(self, request) -> AuthenticatedUser:
        user = AuthenticatedUser(
            user_id="user-123",
            subject="user-123",
            claims={"sub": "user-123"},
            is_authenticated=True,
        )
        request.state.current_user = user
        return user


def test_graphs_api_lists_plan_analyze() -> None:
    app = create_app()
    app.state.logto_authenticator = FakeAuthenticator()

    with TestClient(app) as client:
        response = client.get("/api/graphs")

    assert response.status_code == 200
    graph_names = {item["name"] for item in response.json()}
    assert "plan-analyze" in graph_names


def test_plan_analyze_graph_returns_analysis() -> None:
    runtime = PlanAnalyzeGraphBuilder().build()
    fake_model = FakeChatModel(
        responses={
            "analysis": "The request needs a workflow template with clear stages.",
        }
    )
    graph_service = GraphService(
        GraphRegistry({runtime.name: runtime}),
        FakeLLMRouter(fake_model),
    )

    result = asyncio.run(
        graph_service.invoke(
            graph_name="plan-analyze",
            payload={
                "query": "Generate a reusable workflow template.",
                "session_id": "demo-session",
            },
        )
    )

    assert result.output.plan == ""
    assert (
        result.output.analysis
        == "The request needs a workflow template with clear stages."
    )
    assert len(fake_model.calls) == 1

    analysis_call = fake_model.calls[0]
    assert analysis_call["tags"] == [
        "graph:plan-analyze",
        "profile:tool_calling",
        "binding:analysis",
        "analysis",
    ]
    assert analysis_call["metadata"] == {
        "graph_name": "plan-analyze",
        "profile": "tool_calling",
        "binding": "analysis",
    }
    analysis_message = analysis_call["messages"][1]
    assert isinstance(analysis_message, HumanMessage)
    assert analysis_message.content == (
        "User request:\nGenerate a reusable workflow template.\n\n"
        "Draft plan:\n\n\n"
        "Provide the final analysis:"
    )


def test_plan_analyze_graph_empty_query_skips_model_calls() -> None:
    runtime = PlanAnalyzeGraphBuilder().build()
    fake_model = FakeChatModel()
    graph_service = GraphService(
        GraphRegistry({runtime.name: runtime}),
        FakeLLMRouter(fake_model),
    )

    result = asyncio.run(
        graph_service.invoke(
            graph_name="plan-analyze",
            payload={
                "query": "   ",
                "session_id": "demo-session",
            },
        )
    )

    assert result.output.plan == ""
    assert result.output.analysis == "No query provided."
    assert fake_model.calls == []


def test_plan_analyze_graph_rejects_invalid_query_type() -> None:
    runtime = PlanAnalyzeGraphBuilder().build()
    graph_service = GraphService(
        GraphRegistry({runtime.name: runtime}),
        FakeLLMRouter(FakeChatModel()),
    )

    with pytest.raises(GraphPayloadValidationError):
        asyncio.run(
            graph_service.invoke(
                graph_name="plan-analyze",
                payload={
                    "query": ["not", "a", "string"],
                    "session_id": "demo-session",
                },
            )
        )


def test_plan_analyze_graph_can_loop_through_tools_when_mcp_enabled() -> None:
    @tool("sport_lookup")
    def sport_lookup() -> str:
        """Return a sport answer."""

        return "sport-result"

    class ToolLoopModel(FakeChatModel):
        async def ainvoke(self, messages: list[object]) -> AIMessage:
            self.calls.append(
                {
                    "messages": messages,
                    "tags": list(self.tags),
                    "metadata": dict(self.metadata),
                }
            )
            if any(isinstance(message, ToolMessage) for message in messages):
                return AIMessage(content="Integrated sport result.")
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "sport_lookup",
                        "args": {},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            )

    class Resolver:
        async def resolve_tools(
            self,
            *,
            graph_name: str,
            server_names,
            current_user,
            request_headers,
        ):
            return [sport_lookup]

    runtime = replace(PlanAnalyzeGraphBuilder().build(), mcp_servers=("sport",))
    fake_model = ToolLoopModel()
    graph_service = GraphService(
        GraphRegistry({runtime.name: runtime}),
        FakeLLMRouter(fake_model),
        mcp_tool_resolver=Resolver(),
    )

    result = asyncio.run(
        graph_service.invoke(
            graph_name="plan-analyze",
            payload={"query": "你好", "session_id": "demo-session"},
            request_context=GraphRequestContext(
                current_user=AuthenticatedUser(
                    user_id="user-123",
                    subject="user-123",
                    claims={"sub": "user-123"},
                    is_authenticated=True,
                ),
                request_headers={"Authorization": "Bearer test-token"},
            ),
        )
    )

    assert fake_model.bound_tool_names == ["sport_lookup"]
    assert len(fake_model.calls) == 2
    assert result.output.analysis == "Integrated sport result."
