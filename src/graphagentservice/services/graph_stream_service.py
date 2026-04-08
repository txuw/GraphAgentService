from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, replace
from typing import Any
from uuid import uuid4

from graphagentservice.common.auth import AuthenticatedUser
from graphagentservice.common.logging import bind_log_context, context_extra, reset_log_context
from graphagentservice.common.trace import resolve_request_trace_context
from graphagentservice.graphs.registry import GraphNotFoundError
from graphagentservice.llm import ChatModelBuildError
from graphagentservice.mcp import MCPConfigurationError, MCPToolResolutionError

from .graph_service import (
    GraphPayloadValidationError,
    GraphRequestContext,
    GraphService,
)
from .stream_event_bus import InProcessStreamEventBus
from .stream_events import LangGraphStreamAdapter, StreamEventFactory, StreamEventSequence, StreamEventTarget
from .tool_execution import ToolStreamEventEmitter
from .sse import SseConnectionRegistry

_logger = logging.getLogger(__name__)


@dataclass(slots=True)
class GraphStreamAccepted:
    graph_name: str
    session_id: str
    page_id: str | None
    request_id: str
    trace_id: str = ""


class GraphStreamDispatchService:
    def __init__(
        self,
        graph_service: GraphService,
        bus: InProcessStreamEventBus,
        sse_connection_registry: SseConnectionRegistry,
    ) -> None:
        self._graph_service = graph_service
        self._bus = bus
        self._sse_connection_registry = sse_connection_registry
        self._tasks: set[asyncio.Task[None]] = set()

    async def execute(
        self,
        *,
        graph_name: str,
        payload: dict[str, Any],
        session_id: str,
        page_id: str | None = None,
        request_id: str | None = None,
        request_context: GraphRequestContext | None = None,
    ) -> GraphStreamAccepted:
        resolved_user_id = self._resolve_user_id(request_context)
        self._sse_connection_registry.require_connections(
            session_id=session_id,
            user_id=resolved_user_id,
            page_id=page_id,
        )

        resolved_request_id = request_id or uuid4().hex
        resolved_request_context = self._normalize_request_context(
            request_context=request_context,
            session_id=session_id,
            request_id=resolved_request_id,
            page_id=page_id,
        )
        resolved_trace_id = self._resolve_trace_id(resolved_request_context)
        _logger.info(
            "Graph stream accepted",
            extra=context_extra(
                event="graph_stream_accepted",
                graph=graph_name,
                sessionId=session_id,
                requestId=resolved_request_id,
                pageId=page_id,
                userId=resolved_user_id,
                status="accepted",
            ),
        )
        task = asyncio.create_task(
            self._run_stream(
                graph_name=graph_name,
                payload=dict(payload),
                session_id=session_id,
                page_id=page_id,
                user_id=resolved_user_id,
                request_id=resolved_request_id,
                trace_id=resolved_trace_id,
                request_context=resolved_request_context,
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

    async def resume(
        self,
        *,
        graph_name: str,
        resume_value: dict[str, Any],
        session_id: str,
        page_id: str | None = None,
        request_id: str | None = None,
        request_context: GraphRequestContext | None = None,
    ) -> GraphStreamAccepted:
        """恢复被中断的 graph（interrupt 后提交用户答案）。"""
        resolved_user_id = self._resolve_user_id(request_context)
        self._sse_connection_registry.require_connections(
            session_id=session_id,
            user_id=resolved_user_id,
            page_id=page_id,
        )

        resolved_request_id = request_id or uuid4().hex
        resolved_request_context = self._normalize_request_context(
            request_context=request_context,
            session_id=session_id,
            request_id=resolved_request_id,
            page_id=page_id,
        )
        resolved_trace_id = self._resolve_trace_id(resolved_request_context)
        _logger.info(
            "Graph resume accepted",
            extra=context_extra(
                event="graph_resume_accepted",
                graph=graph_name,
                sessionId=session_id,
                requestId=resolved_request_id,
                pageId=page_id,
                userId=resolved_user_id,
                status="accepted",
            ),
        )
        task = asyncio.create_task(
            self._run_resume_stream(
                graph_name=graph_name,
                resume_value=dict(resume_value),
                session_id=session_id,
                page_id=page_id,
                user_id=resolved_user_id,
                request_id=resolved_request_id,
                trace_id=resolved_trace_id,
                request_context=resolved_request_context,
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
        page_id: str | None,
        user_id: str | None,
        request_id: str,
        trace_id: str,
        request_context: GraphRequestContext | None,
    ) -> None:
        target = StreamEventTarget(
            graph_name=graph_name,
            session_id=session_id,
            request_id=request_id,
            trace_id=trace_id,
            user_id=user_id,
            page_id=page_id,
        )
        sequence = StreamEventSequence()
        factory = StreamEventFactory(target=target, sequence=sequence)
        adapter = LangGraphStreamAdapter(factory=factory)
        tool_emitter = ToolStreamEventEmitter(factory=factory, bus=self._bus)
        request_context = _attach_tool_stream_emitter(request_context, tool_emitter)
        token = bind_log_context(
            traceId=trace_id or "-",
            graph=graph_name,
            sessionId=session_id,
            requestId=request_id,
            pageId=page_id or "-",
            userId=user_id or "-",
        )
        started = time.perf_counter()

        try:
            _logger.info(
                "Graph stream task started",
                extra=context_extra(event="graph_stream_task_started", status="started"),
            )
            await self._bus.publish(factory.build_request_accepted())

            async for graph_event in self._graph_service.stream_events(
                graph_name=graph_name,
                payload=payload,
                session_id=session_id,
                request_context=request_context,
            ):
                events = adapter.adapt(graph_event.event, graph_event.data)
                await self._bus.publish_many(events)

        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            _logger.exception(
                "Graph stream task failed",
                extra=context_extra(
                    event="graph_stream_task_failed",
                    status="failed",
                    elapsedMs=elapsed_ms,
                    errorType=type(exc).__name__,
                ),
            )
            await self._bus.publish(
                factory.build_error(
                    code=_error_code(exc),
                    message=_error_message(exc),
                    retriable=_is_retriable(exc),
                )
            )
        else:
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            _logger.info(
                "Graph stream task completed",
                extra=context_extra(
                    event="graph_stream_task_completed",
                    status="completed",
                    elapsedMs=elapsed_ms,
                ),
            )
        finally:
            reset_log_context(token)

    async def _run_resume_stream(
        self,
        *,
        graph_name: str,
        resume_value: dict[str, Any],
        session_id: str,
        page_id: str | None,
        user_id: str | None,
        request_id: str,
        trace_id: str,
        request_context: GraphRequestContext | None,
    ) -> None:
        """Resume 后台流：调用 GraphService.resume_stream_events。"""
        target = StreamEventTarget(
            graph_name=graph_name,
            session_id=session_id,
            request_id=request_id,
            trace_id=trace_id,
            user_id=user_id,
            page_id=page_id,
        )
        sequence = StreamEventSequence()
        factory = StreamEventFactory(target=target, sequence=sequence)
        adapter = LangGraphStreamAdapter(factory=factory)
        tool_emitter = ToolStreamEventEmitter(factory=factory, bus=self._bus)
        request_context = _attach_tool_stream_emitter(request_context, tool_emitter)
        token = bind_log_context(
            traceId=trace_id or "-",
            graph=graph_name,
            sessionId=session_id,
            requestId=request_id,
            pageId=page_id or "-",
            userId=user_id or "-",
        )
        started = time.perf_counter()

        try:
            _logger.info(
                "Graph resume task started",
                extra=context_extra(event="graph_resume_task_started", status="started"),
            )
            await self._bus.publish(factory.build_request_accepted())

            async for graph_event in self._graph_service.resume_stream_events(
                graph_name=graph_name,
                session_id=session_id,
                resume_value=resume_value,
                request_context=request_context,
            ):
                events = adapter.adapt(graph_event.event, graph_event.data)
                await self._bus.publish_many(events)

        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            _logger.exception(
                "Graph resume task failed",
                extra=context_extra(
                    event="graph_resume_task_failed",
                    status="failed",
                    elapsedMs=elapsed_ms,
                    errorType=type(exc).__name__,
                ),
            )
            await self._bus.publish(
                factory.build_error(
                    code=_error_code(exc),
                    message=_error_message(exc),
                    retriable=_is_retriable(exc),
                )
            )
        else:
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            _logger.info(
                "Graph resume task completed",
                extra=context_extra(
                    event="graph_resume_task_completed",
                    status="completed",
                    elapsedMs=elapsed_ms,
                ),
            )
        finally:
            reset_log_context(token)

    @staticmethod
    def _resolve_trace_id(request_context: GraphRequestContext | None) -> str:
        if request_context is None:
            return ""
        request_headers = dict(request_context.request_headers)
        if request_context.trace_id:
            return request_context.trace_id
        return resolve_request_trace_context(request_headers).trace_id

    @staticmethod
    def _resolve_user_id(request_context: GraphRequestContext | None) -> str | None:
        if request_context is None:
            return None
        user_id = request_context.current_user.user_id
        if not isinstance(user_id, str):
            return None
        candidate = user_id.strip()
        return candidate or None

    @staticmethod
    def _normalize_request_context(
        *,
        request_context: GraphRequestContext | None,
        session_id: str,
        request_id: str,
        page_id: str | None,
    ) -> GraphRequestContext | None:
        if request_context is None:
            return None
        return replace(
            request_context,
            session_id=session_id,
            request_id=request_id,
            page_id=page_id or "",
        )


def _attach_tool_stream_emitter(
    request_context: GraphRequestContext | None,
    emitter: ToolStreamEventEmitter,
) -> GraphRequestContext:
    if request_context is None:
        return GraphRequestContext(
            current_user=AuthenticatedUser.anonymous(),
            trace_id=emitter.factory.target.trace_id,
            request_headers={},
            session_id=emitter.factory.target.session_id,
            request_id=emitter.factory.target.request_id,
            page_id=emitter.factory.target.page_id or "",
            tool_stream_emitter=emitter,
        )
    return replace(request_context, tool_stream_emitter=emitter)


def _error_code(exc: Exception) -> str:
    if isinstance(exc, GraphNotFoundError):
        return "GRAPH_NOT_FOUND"
    if isinstance(exc, GraphPayloadValidationError):
        return "INVALID_PAYLOAD"
    if isinstance(exc, ChatModelBuildError):
        return "MODEL_BUILD_ERROR"
    if isinstance(exc, (MCPConfigurationError, MCPToolResolutionError)):
        return "MCP_ERROR"
    return "AI_STREAM_ERROR"


def _error_message(exc: Exception) -> str:
    if isinstance(exc, GraphPayloadValidationError):
        return str(exc.errors)
    return str(exc)


def _is_retriable(exc: Exception) -> bool:
    return not isinstance(exc, GraphPayloadValidationError)


def graph_stream_payload_from_input(payload: dict[str, Any] | Any) -> dict[str, Any]:
    if hasattr(payload, "graph_payload"):
        dumped = payload.graph_payload()
        return {str(key): value for key, value in dumped.items()}
    if hasattr(payload, "model_dump"):
        dumped = payload.model_dump()
        return {str(key): value for key, value in dumped.items()}
    if isinstance(payload, dict):
        return {str(key): value for key, value in payload.items()}
    return dict(payload)
