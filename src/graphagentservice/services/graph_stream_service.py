from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from graphagentservice.common.trace import resolve_request_trace_context
from graphagentservice.graphs.registry import GraphNotFoundError
from graphagentservice.llm import ChatModelBuildError
from graphagentservice.mcp import MCPConfigurationError, MCPToolResolutionError

from .graph_service import (
    GraphPayloadValidationError,
    GraphRequestContext,
    GraphService,
)
from .sse import SseConnectionNotFoundError, SseConnectionRegistry


@dataclass(slots=True)
class GraphStreamAccepted:
    graph_name: str
    session_id: str
    page_id: str
    request_id: str
    trace_id: str = ""


class GraphStreamDispatchService:
    def __init__(
        self,
        graph_service: GraphService,
        sse_connection_registry: SseConnectionRegistry,
    ) -> None:
        self._graph_service = graph_service
        self._sse_connection_registry = sse_connection_registry
        self._tasks: set[asyncio.Task[None]] = set()

    async def execute(
        self,
        *,
        graph_name: str,
        payload: dict[str, Any],
        session_id: str,
        page_id: str,
        request_id: str | None = None,
        request_context: GraphRequestContext | None = None,
    ) -> GraphStreamAccepted:
        self._sse_connection_registry.require(session_id=session_id, page_id=page_id)

        resolved_request_id = request_id or uuid4().hex
        resolved_trace_id = self._resolve_trace_id(request_context)
        task = asyncio.create_task(
            self._run_stream(
                graph_name=graph_name,
                payload=dict(payload),
                session_id=session_id,
                page_id=page_id,
                request_id=resolved_request_id,
                trace_id=resolved_trace_id,
                request_context=request_context,
            )
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

        return GraphStreamAccepted(
            graph_name=graph_name,
            session_id=session_id,
            page_id=page_id,
            request_id=resolved_request_id,
            trace_id=resolved_trace_id,
        )

    async def _run_stream(
        self,
        *,
        graph_name: str,
        payload: dict[str, Any],
        session_id: str,
        page_id: str,
        request_id: str,
        trace_id: str,
        request_context: GraphRequestContext | None,
    ) -> None:
        try:
            async for event in self._graph_service.stream_events(
                graph_name=graph_name,
                payload=payload,
                session_id=session_id,
                request_context=request_context,
            ):
                await self._sse_connection_registry.send(
                    session_id=session_id,
                    page_id=page_id,
                    event=event.event,
                    payload=self._wrap_event(
                        graph_name=graph_name,
                        session_id=session_id,
                        page_id=page_id,
                        request_id=request_id,
                        trace_id=trace_id,
                        payload=event.data,
                    ),
                )
        except SseConnectionNotFoundError:
            return
        except Exception as exc:
            await self._send_execution_error(
                graph_name=graph_name,
                session_id=session_id,
                page_id=page_id,
                request_id=request_id,
                trace_id=trace_id,
                exc=exc,
            )

    async def _send_execution_error(
        self,
        *,
        graph_name: str,
        session_id: str,
        page_id: str,
        request_id: str,
        trace_id: str,
        exc: Exception,
    ) -> None:
        try:
            await self._sse_connection_registry.send(
                session_id=session_id,
                page_id=page_id,
                event="error",
                payload=self._wrap_event(
                    graph_name=graph_name,
                    session_id=session_id,
                    page_id=page_id,
                    request_id=request_id,
                    trace_id=trace_id,
                    payload=self._error_payload(exc),
                ),
            )
        except SseConnectionNotFoundError:
            return

    @staticmethod
    def _wrap_event(
        *,
        graph_name: str,
        session_id: str,
        page_id: str,
        request_id: str,
        trace_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        wrapped_payload = {
            "graph_name": graph_name,
            "session_id": session_id,
            "page_id": page_id,
            "request_id": request_id,
            "payload": payload,
        }
        if trace_id:
            wrapped_payload["trace_id"] = trace_id
        return wrapped_payload

    @staticmethod
    def _error_payload(exc: Exception) -> dict[str, object]:
        if isinstance(exc, GraphPayloadValidationError):
            return {"detail": exc.errors}
        if isinstance(exc, GraphNotFoundError):
            return {"detail": str(exc)}
        if isinstance(exc, ChatModelBuildError):
            return {"detail": str(exc)}
        if isinstance(exc, (MCPConfigurationError, MCPToolResolutionError)):
            return {"detail": str(exc)}
        return {"detail": str(exc)}

    @staticmethod
    def _resolve_trace_id(request_context: GraphRequestContext | None) -> str:
        if request_context is None:
            return ""

        request_headers = dict(request_context.request_headers)
        if request_context.trace_id:
            return request_context.trace_id
        return resolve_request_trace_context(request_headers).trace_id


def graph_stream_payload_from_input(payload: dict[str, Any] | Any) -> dict[str, Any]:
    if hasattr(payload, "model_dump"):
        dumped = payload.model_dump()
        return {str(key): value for key, value in dumped.items()}
    if isinstance(payload, dict):
        return {str(key): value for key, value in payload.items()}
    return dict(payload)
