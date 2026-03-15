from fastapi.testclient import TestClient

from overmindagent.graphs import GraphNotFoundError
from overmindagent.main import app
from overmindagent.schemas.analysis import StructuredTextAnalysis, TextAnalysisOutput
from overmindagent.services import GraphInvocationResult


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
