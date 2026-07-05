from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from io import BytesIO
from typing import Any
from urllib.parse import urlparse

import httpx
from PIL import Image, UnidentifiedImageError

_logger = logging.getLogger(__name__)

_DATA_IMAGE_PREFIX = "data:image/"
_FORMAT_TO_MIME = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}
_MAGIC_SIGNATURES = {
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/webp": (b"RIFF",),
}


class ImageFetchError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ImageFetchSettings:
    enabled: bool = False
    graphs: frozenset[str] = frozenset()
    allowed_hosts: frozenset[str] = frozenset()
    timeout_seconds: float = 10.0
    max_bytes: int = 10 * 1024 * 1024
    max_pixels: int = 12_000_000
    allowed_mime_types: frozenset[str] = frozenset(
        {"image/jpeg", "image/png", "image/webp"}
    )

    @classmethod
    def from_settings(cls, settings: Any) -> ImageFetchSettings:
        raw = settings or {}
        return cls(
            enabled=bool(_read(raw, "enabled", default=False)),
            graphs=frozenset(_read_string_list(raw, "graphs")),
            allowed_hosts=frozenset(
                host.lower() for host in _read_string_list(raw, "allowed_hosts")
            ),
            timeout_seconds=float(_read(raw, "timeout_seconds", default=10.0)),
            max_bytes=int(_read(raw, "max_bytes", default=10 * 1024 * 1024)),
            max_pixels=int(_read(raw, "max_pixels", default=12_000_000)),
            allowed_mime_types=frozenset(
                mime.lower() for mime in _read_string_list(
                    raw,
                    "allowed_mime_types",
                    default=("image/jpeg", "image/png", "image/webp"),
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class ProcessedImage:
    data_url: str
    mime_type: str
    byte_count: int
    width: int
    height: int


class ImageInputProcessor:
    def __init__(
        self,
        settings: ImageFetchSettings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._client = client

    async def process_graph_payload(
        self,
        *,
        graph_name: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        if not self._should_process(graph_name):
            return payload

        raw_image_url = payload.get("image_url")
        if not isinstance(raw_image_url, str) or not raw_image_url.strip():
            return payload

        processed = await self.process_image_url(raw_image_url.strip(), graph_name=graph_name)
        next_payload = dict(payload)
        next_payload["image_url"] = processed.data_url
        return next_payload

    async def process_image_url(self, image_url: str, *, graph_name: str) -> ProcessedImage:
        if _is_data_image_url(image_url):
            return ProcessedImage(
                data_url=image_url,
                mime_type=_data_url_mime_type(image_url),
                byte_count=0,
                width=0,
                height=0,
            )

        parsed = urlparse(image_url)
        if parsed.scheme not in {"http", "https"}:
            raise ImageFetchError("image_url must be an http(s) URL or image data URL")

        host = (parsed.hostname or "").lower()
        if not host or host not in self._settings.allowed_hosts:
            raise ImageFetchError("image_url host is not allowed")

        if not self._settings.allowed_hosts:
            raise ImageFetchError("image_fetch.allowed_hosts must not be empty")

        data, content_type = await self._download_image(image_url, host=host)
        actual_mime, width, height = self._validate_image(data, content_type)
        data_url = f"data:{actual_mime};base64,{base64.b64encode(data).decode('ascii')}"
        _logger.info(
            "Image fetched and encoded  graph=%s  host=%s  mime=%s  bytes=%d  width=%d  height=%d",
            graph_name,
            host,
            actual_mime,
            len(data),
            width,
            height,
        )
        return ProcessedImage(
            data_url=data_url,
            mime_type=actual_mime,
            byte_count=len(data),
            width=width,
            height=height,
        )

    def _should_process(self, graph_name: str) -> bool:
        return self._settings.enabled and graph_name in self._settings.graphs

    async def _download_image(self, image_url: str, *, host: str) -> tuple[bytes, str]:
        client = self._client
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._settings.timeout_seconds),
                follow_redirects=False,
            )
        try:
            async with client.stream("GET", image_url) as response:
                if response.status_code != 200:
                    raise ImageFetchError(f"image fetch failed with status {response.status_code}")

                content_type = _normalize_content_type(
                    response.headers.get("content-type", "")
                )
                if content_type not in self._settings.allowed_mime_types:
                    raise ImageFetchError("image content type is not allowed")

                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > self._settings.max_bytes:
                        raise ImageFetchError("image exceeds max_bytes")
                    chunks.append(chunk)
                return b"".join(chunks), content_type
        except httpx.HTTPError as exc:
            raise ImageFetchError(f"image fetch failed for host {host}") from exc
        finally:
            if owns_client:
                await client.aclose()

    def _validate_image(self, data: bytes, content_type: str) -> tuple[str, int, int]:
        if not data:
            raise ImageFetchError("image response body is empty")
        if not _magic_matches(content_type, data):
            raise ImageFetchError("image magic bytes do not match content type")

        try:
            with Image.open(BytesIO(data)) as image:
                image.verify()
            with Image.open(BytesIO(data)) as image:
                actual_mime = _FORMAT_TO_MIME.get(str(image.format).upper())
                width, height = image.size
        except (UnidentifiedImageError, OSError) as exc:
            raise ImageFetchError("image cannot be decoded") from exc

        if actual_mime is None or actual_mime not in self._settings.allowed_mime_types:
            raise ImageFetchError("decoded image format is not allowed")
        if actual_mime != content_type:
            raise ImageFetchError("decoded image format does not match content type")
        if width <= 0 or height <= 0:
            raise ImageFetchError("image dimensions are invalid")
        if width * height > self._settings.max_pixels:
            raise ImageFetchError("image exceeds max_pixels")
        return actual_mime, width, height


def _read(settings: Any, name: str, *, default: Any = None) -> Any:
    if hasattr(settings, "get"):
        value = settings.get(name)
        if value is not None:
            return value
    return default


def _read_string_list(
    settings: Any,
    name: str,
    *,
    default: tuple[str, ...] = (),
) -> tuple[str, ...]:
    value = _read(settings, name, default=default)
    if isinstance(value, str):
        candidate = value.strip()
        return (candidate,) if candidate else ()
    if not isinstance(value, (list, tuple, set, frozenset)):
        return tuple(default)
    return tuple(str(item).strip() for item in value if str(item).strip())


def _is_data_image_url(value: str) -> bool:
    return value.lower().startswith(_DATA_IMAGE_PREFIX) and ";base64," in value.lower()


def _data_url_mime_type(value: str) -> str:
    prefix = value.split(",", 1)[0]
    mime = prefix.removeprefix("data:").split(";", 1)[0].lower()
    return mime or "image/unknown"


def _normalize_content_type(value: str) -> str:
    return value.split(";", 1)[0].strip().lower()


def _magic_matches(content_type: str, data: bytes) -> bool:
    if content_type == "image/webp":
        return data.startswith(b"RIFF") and data[8:12] == b"WEBP"
    return any(data.startswith(signature) for signature in _MAGIC_SIGNATURES.get(content_type, ()))
