from __future__ import annotations

import logging
import time
from typing import Any

from starlette.types import ASGIApp, Receive, Scope, Send

from graphagentservice.common.logging import reset_log_trace_id, set_log_trace_id
from graphagentservice.common.trace import resolve_request_trace_context

_logger = logging.getLogger(__name__)


class RequestLoggingMiddleware:
    """Pure-ASGI middleware that:

    1. Resolves (or generates) a ``trace_id`` from the incoming request headers
       and binds it to the logging context for the duration of the request.
    2. Logs HTTP request start and completion (method, path, status, duration).

    Uses a raw ASGI ``__call__`` rather than ``BaseHTTPMiddleware`` so that
    streaming responses (e.g. SSE) are never buffered.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        # Decode headers once; resolve / generate trace_id
        raw_headers: list[tuple[bytes, bytes]] = scope.get("headers", [])
        headers: dict[str, str] = {k.decode("latin-1"): v.decode("latin-1") for k, v in raw_headers}
        trace_context = resolve_request_trace_context(headers)
        token = set_log_trace_id(trace_context.trace_id)

        method: str = scope.get("method", "")
        path: str = scope.get("path", "")
        client: tuple[str, int] | None = scope.get("client")
        client_host = client[0] if client else "-"

        start = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Any) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 500)
            await send(message)

        _logger.info("--> %s %s  client=%s", method, path, client_host)
        try:
            await self._app(scope, receive, send_wrapper)
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            _logger.info(
                "<-- %s %s  status=%d  elapsed=%.1fms",
                method, path, status_code, elapsed_ms,
            )
            reset_log_trace_id(token)
