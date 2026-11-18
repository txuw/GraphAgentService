from __future__ import annotations

import asyncio
from io import BytesIO
from typing import Any

import httpx
import pytest
from PIL import Image
from pydantic import BaseModel, Field

from graphagentservice.graphs.runtime import GraphRuntime
from graphagentservice.graphs.registry import GraphRegistry
from graphagentservice.common.logging import fmt_payload
from graphagentservice.services.graph_service import GraphService
from graphagentservice.services.image_input import (
    ImageFetchError,
    ImageFetchSettings,
    ImageInputProcessor,
)


class _ImageRequest(BaseModel):
    image_url: str = Field(min_length=1)
    text: str = ""


class _ImageOutput(BaseModel):
    image_url: str


class _FakeGraph:
    def __init__(self) -> None:
        self.last_input: dict[str, Any] | None = None

    async def ainvoke(
        self,
        graph_input: dict[str, Any],
        **_: Any,
    ) -> dict[str, Any]:
        self.last_input = graph_input
        return {"image_url": str(graph_input["image_url"])}

    async def astream(
        self,
        graph_input: dict[str, Any],
        **_: Any,
    ):
        self.last_input = graph_input
        yield {"type": "values", "data": {"image_url": str(graph_input["image_url"])}}


class _FakeRouter:
    pass


def _image_bytes(fmt: str = "JPEG", *, size: tuple[int, int] = (2, 2)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, color=(128, 128, 128)).save(buffer, format=fmt)
    return buffer.getvalue()


def _client_for_response(
    *,
    status_code: int = 200,
    content: bytes | None = None,
    content_type: str = "image/jpeg",
) -> httpx.AsyncClient:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            headers={"content-type": content_type},
            content=content if content is not None else _image_bytes(),
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _processor(
    *,
    client: httpx.AsyncClient | None = None,
    max_bytes: int = 10 * 1024 * 1024,
    max_pixels: int = 12_000_000,
    allowed_hosts: frozenset[str] = frozenset({"cdn.example.test"}),
    url_passthrough_graphs: frozenset[str] = frozenset(),
) -> ImageInputProcessor:
    return ImageInputProcessor(
        ImageFetchSettings(
            enabled=True,
            graphs=frozenset({"body-report-analyze", "image-analyze-calories"}),
            allowed_hosts=allowed_hosts,
            max_bytes=max_bytes,
            max_pixels=max_pixels,
            url_passthrough_graphs=url_passthrough_graphs,
        ),
        client=client,
    )


def test_process_image_url_downloads_valid_jpeg_as_data_url() -> None:
    processor = _processor(client=_client_for_response())

    result = asyncio.run(
        processor.process_image_url(
            "https://cdn.example.test/image.jpg?x-oss-process=resize",
            graph_name="image-analyze-calories",
        )
    )

    assert result.data_url.startswith("data:image/jpeg;base64,")
    assert result.mime_type == "image/jpeg"
    assert result.byte_count > 0
    assert (result.width, result.height) == (2, 2)


def test_process_image_url_skips_existing_data_url() -> None:
    processor = _processor(client=_client_for_response(status_code=500))
    data_url = "data:image/jpeg;base64,abc"

    result = asyncio.run(
        processor.process_image_url(data_url, graph_name="image-analyze-calories")
    )

    assert result.data_url == data_url
    assert result.mime_type == "image/jpeg"


def test_process_image_url_rejects_disallowed_host() -> None:
    processor = _processor(client=_client_for_response())

    try:
        asyncio.run(
            processor.process_image_url(
                "https://evil.example.test/image.jpg",
                graph_name="image-analyze-calories",
            )
        )
    except ImageFetchError as exc:
        assert "host is not allowed" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ImageFetchError")


def test_process_image_url_rejects_non_200_response() -> None:
    processor = _processor(client=_client_for_response(status_code=404))

    try:
        asyncio.run(
            processor.process_image_url(
                "https://cdn.example.test/missing.jpg",
                graph_name="image-analyze-calories",
            )
        )
    except ImageFetchError as exc:
        assert "status 404" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ImageFetchError")


def test_process_image_url_rejects_disallowed_content_type() -> None:
    processor = _processor(client=_client_for_response(content_type="text/plain"))

    try:
        asyncio.run(
            processor.process_image_url(
                "https://cdn.example.test/image.txt",
                graph_name="image-analyze-calories",
            )
        )
    except ImageFetchError as exc:
        assert "content type" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ImageFetchError")


def test_process_image_url_rejects_magic_mismatch() -> None:
    processor = _processor(
        client=_client_for_response(
            content=_image_bytes("PNG"),
            content_type="image/jpeg",
        )
    )

    try:
        asyncio.run(
            processor.process_image_url(
                "https://cdn.example.test/image.jpg",
                graph_name="image-analyze-calories",
            )
        )
    except ImageFetchError as exc:
        assert "magic bytes" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ImageFetchError")


def test_process_image_url_rejects_oversized_download() -> None:
    processor = _processor(client=_client_for_response(), max_bytes=8)

    try:
        asyncio.run(
            processor.process_image_url(
                "https://cdn.example.test/image.jpg",
                graph_name="image-analyze-calories",
            )
        )
    except ImageFetchError as exc:
        assert "max_bytes" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ImageFetchError")


def test_process_image_url_rejects_corrupt_image() -> None:
    processor = _processor(
        client=_client_for_response(
            content=b"\xff\xd8\xffnot-a-real-jpeg",
            content_type="image/jpeg",
        )
    )

    try:
        asyncio.run(
            processor.process_image_url(
                "https://cdn.example.test/image.jpg",
                graph_name="body-report-analyze",
            )
        )
    except ImageFetchError as exc:
        assert "decoded" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ImageFetchError")


def test_process_image_url_rejects_excess_pixels() -> None:
    processor = _processor(
        client=_client_for_response(content=_image_bytes(size=(4, 4))),
        max_pixels=4,
    )

    try:
        asyncio.run(
            processor.process_image_url(
                "https://cdn.example.test/image.jpg",
                graph_name="body-report-analyze",
            )
        )
    except ImageFetchError as exc:
        assert "max_pixels" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ImageFetchError")


def _graph_service_for(
    *,
    graph_name: str,
    fake_graph: _FakeGraph,
    processor: ImageInputProcessor,
) -> GraphService:
    runtime = GraphRuntime(
        name=graph_name,
        description="test graph",
        graph=fake_graph,
        input_model=_ImageRequest,
        output_model=_ImageOutput,
    )
    return GraphService(
        GraphRegistry({graph_name: runtime}),
        _FakeRouter(),  # type: ignore[arg-type]
        image_input_processor=processor,
    )


def test_graph_service_invoke_processes_target_graph_image_url() -> None:
    fake_graph = _FakeGraph()
    service = _graph_service_for(
        graph_name="image-analyze-calories",
        fake_graph=fake_graph,
        processor=_processor(client=_client_for_response()),
    )

    result = asyncio.run(
        service.invoke(
            graph_name="image-analyze-calories",
            payload={"image_url": "https://cdn.example.test/image.jpg"},
        )
    )

    assert result.output.image_url.startswith("data:image/jpeg;base64,")
    assert fake_graph.last_input is not None
    assert str(fake_graph.last_input["image_url"]).startswith("data:image/jpeg;base64,")


@pytest.mark.parametrize("image_url", [
    "https://cdn.example.test/image.jpg?x-oss-process=image/auto-orient,1/resize,w_1080,limit_1/quality,q_80",
    "data:image/jpeg;base64,abc",
])
def test_graph_service_invoke_passes_calorie_image_without_fetch(image_url: str) -> None:
    def unexpected_download(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"Unexpected image download: {request.url.host}")

    fake_graph = _FakeGraph()
    service = _graph_service_for(
        graph_name="image-analyze-calories",
        fake_graph=fake_graph,
        processor=_processor(
            client=httpx.AsyncClient(transport=httpx.MockTransport(unexpected_download)),
            url_passthrough_graphs=frozenset({"image-analyze-calories"}),
        ),
    )

    result = asyncio.run(
        service.invoke(graph_name="image-analyze-calories", payload={"image_url": image_url})
    )

    assert result.output.image_url == image_url
    assert fake_graph.last_input is not None
    assert fake_graph.last_input["image_url"] == image_url


@pytest.mark.parametrize("image_url", [
    "https://other.example.test/image.jpg",
    "https://user@cdn.example.test/image.jpg",
    "https://cdn.example.test:8443/image.jpg",
])
def test_url_passthrough_rejects_unapproved_origin(image_url: str) -> None:
    processor = _processor(
        client=_client_for_response(),
        url_passthrough_graphs=frozenset({"image-analyze-calories"}),
    )

    with pytest.raises(ImageFetchError, match="host is not allowed|allowed HTTPS origin"):
        asyncio.run(processor.process_graph_payload(
            graph_name="image-analyze-calories", payload={"image_url": image_url}
        ))


def test_url_passthrough_keeps_http_image_fetch() -> None:
    processor = _processor(
        client=_client_for_response(),
        url_passthrough_graphs=frozenset({"image-analyze-calories"}),
    )

    payload = asyncio.run(processor.process_graph_payload(
        graph_name="image-analyze-calories",
        payload={"image_url": "http://cdn.example.test/image.jpg"},
    ))

    assert str(payload["image_url"]).startswith("data:image/jpeg;base64,")


def test_graph_service_stream_processes_target_graph_image_url() -> None:
    fake_graph = _FakeGraph()
    service = _graph_service_for(
        graph_name="body-report-analyze",
        fake_graph=fake_graph,
        processor=_processor(
            client=_client_for_response(),
            url_passthrough_graphs=frozenset({"image-analyze-calories"}),
        ),
    )

    async def collect() -> list[object]:
        events = []
        async for event in service.stream_events(
            graph_name="body-report-analyze",
            payload={"image_url": "https://cdn.example.test/image.jpg"},
        ):
            events.append(event)
        return events

    events = asyncio.run(collect())

    assert events[-2].event == "result"
    assert str(events[-2].data["image_url"]).startswith("data:image/jpeg;base64,")
    assert fake_graph.last_input is not None
    assert str(fake_graph.last_input["image_url"]).startswith("data:image/jpeg;base64,")


def test_graph_service_does_not_process_non_target_graph() -> None:
    fake_graph = _FakeGraph()
    service = _graph_service_for(
        graph_name="image-agent",
        fake_graph=fake_graph,
        processor=_processor(client=_client_for_response(status_code=500)),
    )

    result = asyncio.run(
        service.invoke(
            graph_name="image-agent",
            payload={"image_url": "https://cdn.example.test/image.jpg"},
        )
    )

    assert result.output.image_url == "https://cdn.example.test/image.jpg"


def test_fmt_payload_redacts_image_url_and_data_url() -> None:
    logged = fmt_payload(
        {
            "imageUrl": "https://cdn.example.test/image.jpg?signature=secret",
            "nested": {
                "image_url": "data:image/jpeg;base64,abcdef",
            },
        },
        max_chars=1000,
    )

    assert "signature=secret" not in logged
    assert "abcdef" not in logged
    assert "https://cdn.example.test/<redacted>" in logged
    assert "data:image/jpeg;base64,<redacted>" in logged
