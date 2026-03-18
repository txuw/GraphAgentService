from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

from overmindagent.common.auth import AuthenticatedUser
from overmindagent.graphs.image_agent import ImageGraphBuilder
from overmindagent.graphs.registry import GraphRegistry
from overmindagent.main import create_app
from overmindagent.services.graph_service import GraphPayloadValidationError, GraphService


class FakeChatModel:
    def __init__(self) -> None:
        self.last_messages: list[object] | None = None
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
        self.last_messages = messages
        return AIMessage(content="图片中是一个测试标识。")


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


def test_graphs_api_lists_image_agent() -> None:
    app = create_app()
    app.state.logto_authenticator = FakeAuthenticator()

    with TestClient(app) as client:
        response = client.get("/api/graphs")

    assert response.status_code == 200
    graph_names = {item["name"] for item in response.json()}
    assert "image-agent" in graph_names


def test_image_agent_graph_returns_answer() -> None:
    runtime = ImageGraphBuilder().build()
    fake_model = FakeChatModel()
    graph_service = GraphService(
        GraphRegistry({runtime.name: runtime}),
        FakeLLMRouter(fake_model),
    )

    result = asyncio.run(
        graph_service.invoke(
            graph_name="image-agent",
            payload={
                "text": "这张图片是什么内容",
                "image_url": "https://oss.txuw.top/logo.jpg",
                "session_id": "demo-session",
            },
        )
    )

    assert result.output.answer == "图片中是一个测试标识。"
    assert fake_model.last_messages is not None
    assert fake_model.tags == [
        "graph:image-agent",
        "profile:multimodal",
        "binding:analysis",
        "multimodal",
    ]
    assert fake_model.metadata == {
        "graph_name": "image-agent",
        "profile": "multimodal",
        "binding": "analysis",
    }

    human_message = fake_model.last_messages[1]
    assert isinstance(human_message, HumanMessage)
    assert human_message.content == [
        {"type": "text", "text": "这张图片是什么内容"},
        {
            "type": "image_url",
            "image_url": {"url": "https://oss.txuw.top/logo.jpg"},
        },
    ]


def test_image_agent_graph_requires_image_url() -> None:
    runtime = ImageGraphBuilder().build()
    graph_service = GraphService(
        GraphRegistry({runtime.name: runtime}),
        FakeLLMRouter(FakeChatModel()),
    )

    with pytest.raises(GraphPayloadValidationError):
        asyncio.run(
            graph_service.invoke(
                graph_name="image-agent",
                payload={
                    "text": "这张图片是什么内容",
                    "session_id": "demo-session",
                },
            )
        )
