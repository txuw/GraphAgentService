from fastapi.testclient import TestClient

from overmindagent.main import app


client = TestClient(app)


def test_root_returns_hello_world() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {"message": "Hello World"}


def test_hello_endpoint_returns_name() -> None:
    response = client.get("/hello/uv")

    assert response.status_code == 200
    assert response.json() == {"message": "Hello uv"}


def test_healthz_endpoint_returns_status() -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert "environment" in response.json()
