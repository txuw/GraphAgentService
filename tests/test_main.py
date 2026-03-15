from fastapi.testclient import TestClient

from overmindagent.graphs import GraphNotFoundError
from overmindagent.main import app
from overmindagent.schemas.analysis import StructuredTextAnalysis, TextAnalysisOutput
from overmindagent.services import ChatStreamAccepted, GraphInvocationResult


client = TestClient(app)


def test_root_returns_hello_world() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {"message": "Hello World"}


def test_hello_endpoint_returns_name() -> None:
    response = client.get("/hello/uv")

    assert response.status_code == 200
    assert response.json() == {"message": "Hello uv"}


def test_health_endpoint_returns_status() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert "environment" in response.json()


def test_graph_list_endpoint_returns_runtime_metadata() -> None:
    response = client.get("/api/graphs")

    assert response.status_code == 200
    graph_names = {item["name"] for item in response.json()}
    assert {"text-analysis", "tool-agent"} <= graph_names
    assert all("input_schema" in item for item in response.json())
    assert all("output_schema" in item for item in response.json())
    assert all("stream_modes" in item for item in response.json())


def test_graph_invoke_endpoint_returns_structured_response() -> None:
    original_service = app.state.graph_service

    class FakeGraphService:
        async def invoke(self, graph_name: str, payload):
            return GraphInvocationResult(
                graph_name=graph_name,
                session_id=payload.get("session_id") or "session-1",
                output=TextAnalysisOutput(
                    normalized_text="hello world",
                    analysis=StructuredTextAnalysis(
                        language="en",
                        summary="Greeting",
                        intent="salutation",
                        sentiment="positive",
                        categories=["greeting"],
                        confidence=0.99,
                    ),
                ),
            )

        async def stream(self, graph_name: str, payload):
            yield "event: completed\ndata: {\"session_id\": \"session-1\"}\n\n"

    app.state.graph_service = FakeGraphService()
    try:
        response = client.post(
            "/api/graphs/text-analysis/invoke",
            json={"text": "hello world", "session_id": "session-1"},
        )
    finally:
        app.state.graph_service = original_service

    assert response.status_code == 200
    assert response.json()["graph_name"] == "text-analysis"
    assert response.json()["session_id"] == "session-1"
    assert response.json()["data"]["analysis"]["intent"] == "salutation"


def test_graph_invoke_endpoint_returns_404_for_unknown_graph() -> None:
    original_service = app.state.graph_service

    class MissingGraphService:
        async def invoke(self, graph_name: str, payload):
            raise GraphNotFoundError(graph_name)

        async def stream(self, graph_name: str, payload):
            raise GraphNotFoundError(graph_name)

    app.state.graph_service = MissingGraphService()
    try:
        response = client.post(
            "/api/graphs/unknown/invoke",
            json={"text": "hello world"},
        )
    finally:
        app.state.graph_service = original_service

    assert response.status_code == 404


def test_graph_stream_endpoint_returns_sse_payload() -> None:
    original_service = app.state.graph_service

    class FakeGraphService:
        async def invoke(self, graph_name: str, payload):
            return GraphInvocationResult(
                graph_name=graph_name,
                session_id=payload.get("session_id") or "session-1",
                output=TextAnalysisOutput(
                    normalized_text="hello world",
                    analysis=StructuredTextAnalysis(
                        language="en",
                        summary="Greeting",
                        intent="salutation",
                        sentiment="positive",
                        categories=["greeting"],
                        confidence=0.99,
                    ),
                ),
            )

        async def stream(self, graph_name: str, payload):
            yield "event: session\ndata: {\"graph_name\":\"text-analysis\",\"session_id\":\"session-1\"}\n\n"
            yield "event: completed\ndata: {\"session_id\":\"session-1\"}\n\n"

    app.state.graph_service = FakeGraphService()
    try:
        response = client.post(
            "/api/graphs/text-analysis/stream",
            json={"text": "hello world", "session_id": "session-1"},
        )
    finally:
        app.state.graph_service = original_service

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert "event: session" in response.text


def test_sse_connect_endpoint_returns_connected_event() -> None:
    original_registry = app.state.sse_connection_registry

    class FakeRegistry:
        async def register(self, *, session_id: str, page_id: str, last_event_id: str | None = None):
            assert session_id == "session-1"
            assert page_id == "page-1"
            assert last_event_id == "event-9"
            return object()

        async def send_connected_event(self, connection) -> None:
            return None

        async def event_stream(self, connection, *, is_disconnected=None):
            yield (
                "id: connection-1:1\n"
                "event: connected\n"
                "retry: 3000\n"
                "data: {\"session_id\":\"session-1\",\"page_id\":\"page-1\",\"last_event_id\":\"event-9\"}\n\n"
            )

    app.state.sse_connection_registry = FakeRegistry()
    try:
        response = client.get(
            "/api/sse/connect",
            params={"sessionId": "session-1", "pageId": "page-1"},
            headers={"Last-Event-ID": "event-9"},
        )
    finally:
        app.state.sse_connection_registry = original_registry

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert "event: connected" in response.text
    assert "\"session_id\":\"session-1\"" in response.text
    assert "\"page_id\":\"page-1\"" in response.text
    assert "\"last_event_id\":\"event-9\"" in response.text


def test_chat_execute_endpoint_returns_acknowledgement() -> None:
    original_service = app.state.chat_stream_service

    class FakeChatStreamService:
        async def execute(
            self,
            *,
            graph_name: str,
            payload,
            session_id: str,
            page_id: str,
            request_id: str | None = None,
        ):
            assert graph_name == "tool-agent"
            assert payload == {"query": "hello"}
            assert session_id == "session-1"
            assert page_id == "page-1"
            return ChatStreamAccepted(
                graph_name=graph_name,
                session_id=session_id,
                page_id=page_id,
                request_id=request_id or "request-1",
            )

    app.state.chat_stream_service = FakeChatStreamService()
    try:
        response = client.post(
            "/api/chat/execute",
            json={
                "graphName": "tool-agent",
                "input": {"query": "hello"},
                "sessionId": "session-1",
                "pageId": "page-1",
                "requestId": "request-1",
            },
        )
    finally:
        app.state.chat_stream_service = original_service

    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert response.json()["graph_name"] == "tool-agent"
    assert response.json()["session_id"] == "session-1"
    assert response.json()["page_id"] == "page-1"
    assert response.json()["request_id"] == "request-1"


def test_chat_execute_endpoint_returns_404_when_connection_missing() -> None:
    response = client.post(
        "/api/chat/execute",
        json={
            "graph_name": "tool-agent",
            "input": {"query": "hello"},
            "session_id": "missing-session",
            "page_id": "missing-page",
        },
    )

    assert response.status_code == 404
