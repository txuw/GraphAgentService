from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

from overmindagent.graphs.plan_analyze import PlanAnalyzeGraphBuilder
from overmindagent.graphs.registry import GraphRegistry
from overmindagent.main import create_app
from overmindagent.services.graph_service import GraphPayloadValidationError, GraphService


class FakeChatModel:
    def __init__(self, responses: dict[str, str] | None = None) -> None:
        self._responses = responses or {}
        self.calls: list[dict[str, object]] = []
        self.tags: list[str] = []
        self.metadata: dict[str, object] = {}

    def with_config(
        self,
        *,
        tags: list[str] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> "FakeChatModel":
        self.tags = list(tags or [])
        self.metadata = dict(metadata or {})
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


def test_graphs_api_lists_plan_analyze() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/api/graphs")

    assert response.status_code == 200
    graph_names = {item["name"] for item in response.json()}
    assert "plan-analyze" in graph_names


def test_plan_analyze_graph_returns_plan_and_analysis() -> None:
    runtime = PlanAnalyzeGraphBuilder().build()
    fake_model = FakeChatModel(
        responses={
            "planner": "1. Inspect the request.\n2. Identify key constraints.",
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

    assert result.output.plan == "1. Inspect the request.\n2. Identify key constraints."
    assert (
        result.output.analysis
        == "The request needs a workflow template with clear stages."
    )
    assert len(fake_model.calls) == 2

    planner_call = fake_model.calls[0]
    assert planner_call["tags"] == [
        "graph:plan-analyze",
        "profile:planning",
        "binding:planner",
        "planning",
    ]
    assert planner_call["metadata"] == {
        "graph_name": "plan-analyze",
        "profile": "planning",
        "binding": "planner",
    }
    planner_message = planner_call["messages"][1]
    assert isinstance(planner_message, HumanMessage)
    assert planner_message.content == "User request:\nGenerate a reusable workflow template."

    analysis_call = fake_model.calls[1]
    assert analysis_call["tags"] == [
        "graph:plan-analyze",
        "profile:structured_output",
        "binding:analysis",
        "analysis",
    ]
    assert analysis_call["metadata"] == {
        "graph_name": "plan-analyze",
        "profile": "structured_output",
        "binding": "analysis",
    }
    analysis_message = analysis_call["messages"][1]
    assert isinstance(analysis_message, HumanMessage)
    assert analysis_message.content == (
        "User request:\nGenerate a reusable workflow template.\n\n"
        "Draft plan:\n1. Inspect the request.\n2. Identify key constraints.\n\n"
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
