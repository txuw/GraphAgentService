from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from pydantic import BaseModel

from overmindagent.common import create_checkpoint_provider, get_settings
from overmindagent.common.auth import AuthenticatedUser
from overmindagent.graphs.registry import GraphRegistry, create_graph_registry
from overmindagent.graphs.runtime import GraphRunContext, GraphRuntime
from overmindagent.graphs.tool_agent import ToolAgentGraphBuilder
from overmindagent.graphs.tool_agent.nodes import ToolAgentNodes
from overmindagent.main import create_app
from overmindagent.mcp import (
    MCPClientFactory,
    MCPConfigurationError,
    MCPConnectionSettings,
    MCPHeaderForwarder,
    MCPSettings,
    MCPToolResolutionError,
    MCPToolResolver,
)
from overmindagent.services.chat_stream_service import ChatStreamService
from overmindagent.services.graph_service import (
    GraphRequestContext,
    GraphService,
    GraphStreamEvent,
)


def make_tool(tool_name: str, result: str = "ok"):
    @tool(tool_name)
    def _tool() -> str:
        """Test tool."""

        return result

    return _tool


class FakeToolCallingModel:
    def __init__(self, response: AIMessage | None = None) -> None:
        self._response = response or AIMessage(content="done")
        self.bound_tool_names: list[str] = []

    def bind_tools(self, tools, **kwargs):
        self.bound_tool_names = [tool.name for tool in tools]
        return self

    async def ainvoke(self, messages):
        return self._response


class FakeLLMRouter:
    def __init__(self, model: FakeToolCallingModel | None = None) -> None:
        self._model = model or FakeToolCallingModel()

    @staticmethod
    def resolve_profile(profile: str | None = None) -> SimpleNamespace:
        return SimpleNamespace(name=profile or "default")

    def create_model(
        self,
        *,
        profile: str | None = None,
        tags: tuple[str, ...] = (),
        metadata: dict[str, object] | None = None,
    ) -> FakeToolCallingModel:
        return self._model


class RecordingResolver:
    def __init__(self, tools):
        self._tools = list(tools)
        self.calls: list[dict[str, object]] = []

    async def resolve_tools(
        self,
        *,
        graph_name: str,
        server_names,
        current_user,
        request_headers,
    ):
        self.calls.append(
            {
                "graph_name": graph_name,
                "server_names": tuple(server_names),
                "current_user": current_user,
                "request_headers": dict(request_headers),
            }
        )
        return list(self._tools)


class FailingResolver:
    async def resolve_tools(self, **kwargs):
        raise MCPToolResolutionError("mcp resolve failed")


class CaptureContextGraph:
    def __init__(self) -> None:
        self.context = None

    async def ainvoke(self, payload, config=None, context=None):
        self.context = context
        return {"answer": "ok"}

    async def astream(self, payload, config=None, context=None, stream_mode=None, version=None):
        self.context = context
        yield {"type": "values", "data": {"answer": "ok"}}


class DummyInput(BaseModel):
    query: str


class DummyOutput(BaseModel):
    answer: str = ""


class FakeMultiServerMCPClient:
    created_configs: list[dict[str, dict[str, object]]] = []

    def __init__(self, config):
        self.config = config
        self.created_configs.append(config)

    async def get_tools(self):
        return [make_tool("remote_sport", "remote")]


class FakeGraphService:
    async def stream(self, *, graph_name, payload, request_context=None):
        if False:
            yield ""
        raise MCPToolResolutionError("mcp failed")


class FakeAuthenticator:
    def authenticate_request(self, request) -> AuthenticatedUser:
        user = AuthenticatedUser(
            user_id="user-test",
            subject="user-test",
            claims={"sub": "user-test"},
            is_authenticated=True,
        )
        request.state.current_user = user
        return user


class FakeSseConnectionRegistry:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def require(self, *, session_id: str, page_id: str) -> None:
        return None

    async def send(self, *, session_id: str, page_id: str, event: str, payload: dict[str, object]) -> None:
        self.events.append((event, payload))


class CapturingStreamGraphService:
    def __init__(self) -> None:
        self.request_contexts: list[GraphRequestContext | None] = []

    async def stream_events(self, *, graph_name, payload, request_context=None):
        self.request_contexts.append(request_context)
        yield GraphStreamEvent(
            event="session",
            data={"graph_name": graph_name, "session_id": payload["session_id"]},
        )
        yield GraphStreamEvent(
            event="completed",
            data={"session_id": payload["session_id"]},
        )


def test_mcp_settings_and_graph_runtime_read_from_settings() -> None:
    get_settings.cache_clear()
    settings = get_settings()
    mcp_settings = MCPSettings.model_validate(settings.get("mcp", {}))

    assert mcp_settings.enabled is True
    assert mcp_settings.connections["sport"].url == "http://api.txuw.top/mcp-servers/sport-assistant-mcp"

    graph_registry = create_graph_registry(
        settings=settings,
        checkpoint_provider=create_checkpoint_provider(settings.graph),
    )
    tool_agent_runtime = graph_registry.get("tool-agent")
    text_analysis_runtime = graph_registry.get("text-analysis")

    assert tool_agent_runtime.mcp_servers == ("sport",)
    assert text_analysis_runtime.mcp_servers == ()


def test_mcp_header_forwarder_prefers_request_authorization() -> None:
    forwarder = MCPHeaderForwarder()

    headers = forwarder.build_forward_headers(
        request_headers={"Authorization": "Bearer request-token"},
        connection_headers={
            "Authorization": "Bearer static-token",
            "X-Static": "1",
        },
    )

    assert headers == {
        "Authorization": "Bearer request-token",
        "X-Static": "1",
    }


def test_mcp_client_factory_cache_isolated_by_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeMultiServerMCPClient.created_configs.clear()
    monkeypatch.setattr(
        "overmindagent.mcp.client.load_multi_server_mcp_client",
        lambda: FakeMultiServerMCPClient,
    )

    settings = MCPSettings(
        enabled=True,
        request_timeout=30.0,
        tool_cache_ttl_seconds=300,
        connections={
            "sport": MCPConnectionSettings(
                url="http://example.com/mcp",
                headers={"X-Server": "sport"},
            )
        },
    )
    factory = MCPClientFactory(settings)

    async def run_test() -> None:
        await factory.get_tools_for_servers(
            server_names=("sport",),
            request_headers={"Authorization": "Bearer token-a"},
        )
        await factory.get_tools_for_servers(
            server_names=("sport",),
            request_headers={"Authorization": "Bearer token-a"},
        )
        await factory.get_tools_for_servers(
            server_names=("sport",),
            request_headers={"Authorization": "Bearer token-b"},
        )

    asyncio.run(run_test())

    assert len(FakeMultiServerMCPClient.created_configs) == 2
    assert (
        FakeMultiServerMCPClient.created_configs[0]["sport"]["headers"]["Authorization"]
        == "Bearer token-a"
    )
    assert (
        FakeMultiServerMCPClient.created_configs[1]["sport"]["headers"]["Authorization"]
        == "Bearer token-b"
    )


def test_mcp_tool_resolver_returns_local_only_when_disabled() -> None:
    local_tool = make_tool("local_only")
    resolver = MCPToolResolver(
        MCPSettings(enabled=False),
        local_tools_builder=lambda: [local_tool],
    )

    tools = asyncio.run(
        resolver.resolve_tools(
            graph_name="tool-agent",
            server_names=("sport",),
            current_user=AuthenticatedUser.anonymous(),
            request_headers={},
        )
    )

    assert [tool.name for tool in tools] == ["local_only"]


def test_mcp_tool_resolver_merges_tools_and_preserves_local_conflicts() -> None:
    local_tool = make_tool("shared_tool", "local")
    remote_duplicate = make_tool("shared_tool", "remote")
    remote_unique = make_tool("remote_unique", "remote")

    class FakeFactory:
        async def get_tools_for_servers(self, *, server_names, request_headers):
            return [remote_duplicate, remote_unique]

    resolver = MCPToolResolver(
        MCPSettings(
            enabled=True,
            connections={"sport": MCPConnectionSettings(url="http://example.com/mcp")},
        ),
        client_factory=FakeFactory(),
        local_tools_builder=lambda: [local_tool],
    )

    tools = asyncio.run(
        resolver.resolve_tools(
            graph_name="tool-agent",
            server_names=("sport",),
            current_user=AuthenticatedUser.anonymous(),
            request_headers={},
        )
    )

    assert [tool.name for tool in tools] == ["shared_tool", "remote_unique"]


def test_mcp_tool_resolver_preserves_first_remote_duplicate() -> None:
    first_remote = make_tool("remote_dup", "first")
    second_remote = make_tool("remote_dup", "second")
    remote_unique = make_tool("remote_unique", "unique")

    class FakeFactory:
        async def get_tools_for_servers(self, *, server_names, request_headers):
            return [first_remote, second_remote, remote_unique]

    resolver = MCPToolResolver(
        MCPSettings(
            enabled=True,
            connections={"sport": MCPConnectionSettings(url="http://example.com/mcp")},
        ),
        client_factory=FakeFactory(),
        local_tools_builder=list,
    )

    tools = asyncio.run(
        resolver.resolve_tools(
            graph_name="tool-agent",
            server_names=("sport", "office"),
            current_user=AuthenticatedUser.anonymous(),
            request_headers={},
        )
    )

    assert [tool.name for tool in tools] == ["remote_dup", "remote_unique"]


def test_graph_service_injects_request_context_into_graph_runtime() -> None:
    graph = CaptureContextGraph()
    runtime = GraphRuntime(
        name="dummy",
        description="dummy",
        graph=graph,
        input_model=DummyInput,
        output_model=DummyOutput,
        mcp_servers=("sport",),
    )
    graph_service = GraphService(
        GraphRegistry({"dummy": runtime}),
        FakeLLMRouter(),
        mcp_tool_resolver=RecordingResolver([make_tool("remote_sport")]),
    )
    current_user = AuthenticatedUser(
        user_id="user-1",
        subject="user-1",
        claims={"sub": "user-1"},
        is_authenticated=True,
    )

    asyncio.run(
        graph_service.invoke(
            graph_name="dummy",
            payload={"query": "hello"},
            request_context=GraphRequestContext(
                current_user=current_user,
                request_headers={"Authorization": "Bearer user-token"},
            ),
        )
    )

    assert graph.context is not None
    assert graph.context.current_user == current_user
    assert graph.context.request_headers == {"Authorization": "Bearer user-token"}
    assert graph.context.mcp_servers == ("sport",)


def test_tool_agent_node_uses_local_tools_when_no_resolver() -> None:
    model = FakeToolCallingModel()
    runtime = SimpleNamespace(
        context=GraphRunContext(
            llm_router=FakeLLMRouter(model),
            graph_name="tool-agent",
        ),
        store=None,
        stream_writer=None,
    )
    node = ToolAgentNodes(tools=[make_tool("local_math")])

    result = asyncio.run(
        node.agent(
            {"messages": [HumanMessage(content="calculate")]},
            runtime,
        )
    )

    assert model.bound_tool_names == ["local_math"]
    assert isinstance(result["messages"][0], AIMessage)


def test_tool_agent_resolves_tools_per_request_for_agent_and_tools_node() -> None:
    remote_tool = make_tool("remote_sport", "remote-result")
    resolver = RecordingResolver([remote_tool])
    model = FakeToolCallingModel(
        response=AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "remote_sport",
                    "args": {},
                    "id": "call-1",
                    "type": "tool_call",
                }
            ],
        )
    )
    current_user = AuthenticatedUser(
        user_id="user-2",
        subject="user-2",
        claims={"sub": "user-2"},
        is_authenticated=True,
    )
    runtime = SimpleNamespace(
        context=GraphRunContext(
            llm_router=FakeLLMRouter(model),
            graph_name="tool-agent",
            current_user=current_user,
            request_headers={"Authorization": "Bearer dynamic-token"},
            mcp_tool_resolver=resolver,
            mcp_servers=("sport",),
        ),
        store=None,
        stream_writer=None,
    )
    node = ToolAgentNodes()

    agent_result = asyncio.run(
        node.agent(
            {"messages": [HumanMessage(content="what can you do")]},
            runtime,
        )
    )
    tools_result = asyncio.run(
        node.tools(
            {"messages": list(agent_result["messages"])},
            runtime,
        )
    )

    assert model.bound_tool_names == ["remote_sport"]
    assert len(resolver.calls) == 2
    assert resolver.calls[0]["request_headers"] == {"Authorization": "Bearer dynamic-token"}
    assert resolver.calls[0]["server_names"] == ("sport",)
    assert isinstance(tools_result["messages"][0], ToolMessage)
    assert tools_result["messages"][0].content == "remote-result"


def test_tool_agent_graph_propagates_mcp_resolution_error() -> None:
    runtime = replace(
        ToolAgentGraphBuilder().build(),
        mcp_servers=("sport",),
    )
    graph_service = GraphService(
        GraphRegistry({runtime.name: runtime}),
        FakeLLMRouter(),
        mcp_tool_resolver=FailingResolver(),
    )

    with pytest.raises(MCPToolResolutionError, match="mcp resolve failed"):
        asyncio.run(
            graph_service.invoke(
                graph_name="tool-agent",
                payload={"query": "hello"},
                request_context=GraphRequestContext(
                    current_user=AuthenticatedUser.anonymous(),
                    request_headers={"Authorization": "Bearer token"},
                ),
            )
        )


def test_graph_stream_route_emits_error_event_for_mcp_failure() -> None:
    app = create_app()
    app.state.graph_service = FakeGraphService()
    app.state.logto_authenticator = FakeAuthenticator()

    with TestClient(app) as client:
        response = client.post(
            "/api/graphs/tool-agent/stream",
            json={"query": "hello"},
        )

    assert response.status_code == 200
    assert "event: error" in response.text
    assert "mcp failed" in response.text


def test_chat_stream_service_passes_request_context_to_background_stream() -> None:
    graph_service = CapturingStreamGraphService()
    sse_registry = FakeSseConnectionRegistry()
    chat_stream_service = ChatStreamService(graph_service, sse_registry)
    request_context = GraphRequestContext(
        current_user=AuthenticatedUser(
            user_id="user-3",
            subject="user-3",
            claims={"sub": "user-3"},
            is_authenticated=True,
        ),
        request_headers={"Authorization": "Bearer chat-token"},
    )

    async def run_test() -> None:
        await chat_stream_service.execute(
            graph_name="tool-agent",
            payload={"query": "hello"},
            session_id="session-1",
            page_id="page-1",
            request_id="request-1",
            request_context=request_context,
        )
        if chat_stream_service._tasks:
            await asyncio.gather(*tuple(chat_stream_service._tasks))

    asyncio.run(run_test())

    assert graph_service.request_contexts == [request_context]
    assert sse_registry.events[-1][0] == "ai_done"


def test_mcp_client_factory_rejects_unknown_server() -> None:
    factory = MCPClientFactory(
        MCPSettings(
            enabled=True,
            connections={"sport": MCPConnectionSettings(url="http://example.com/mcp")},
        )
    )

    with pytest.raises(MCPConfigurationError, match="Unknown MCP server"):
        asyncio.run(
            factory.get_tools_for_servers(
                server_names=("unknown",),
                request_headers={},
            )
        )
