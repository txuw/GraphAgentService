from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Any
from uuid import uuid4

from graphagentservice.common.auth import AuthenticatedUser
from graphagentservice.common.trace import resolve_request_trace_context
from graphagentservice.graphs.registry import GraphNotFoundError
from graphagentservice.llm import ChatModelBuildError
from graphagentservice.mcp import MCPConfigurationError, MCPToolResolutionError

from .agent_events import (
    AgentStreamEventAdapter,
    AgentStreamEventFactory,
    RequestEventSequence,
    RequestEventTarget,
    ToolEventEmitter,
)
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
    page_id: str | None
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
        resolved_trace_id = self._resolve_trace_id(request_context)
        task = asyncio.create_task(
            self._run_stream(
                graph_name=graph_name,
                payload=dict(payload),
                session_id=session_id,
                page_id=page_id,
                user_id=resolved_user_id,
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
        page_id: str | None,
        user_id: str | None,
        request_id: str,
        trace_id: str,
        request_context: GraphRequestContext | None,
    ) -> None:
        target = RequestEventTarget(
            graph_name=graph_name,
            session_id=session_id,
            request_id=request_id,
            trace_id=trace_id,
            user_id=user_id,
            page_id=page_id,
        )
        sequence = RequestEventSequence()
        factory = AgentStreamEventFactory(target=target, sequence=sequence)
        adapter = AgentStreamEventAdapter(factory=factory)
        event_loop = asyncio.get_running_loop()
        tool_event_emitter = ToolEventEmitter(
            factory=factory,
            registry=self._sse_connection_registry,
            loop=event_loop,
        )
        request_context = self._attach_tool_event_emitter(
            request_context=request_context,
            tool_event_emitter=tool_event_emitter,
        )

        try:
            for event in adapter.initial_events():
                await self._publish(target=target, event=event)

            async for graph_event in self._graph_service.stream_events(
                graph_name=graph_name,
                payload=payload,
                session_id=session_id,
                request_context=request_context,
            ):
                for event in adapter.adapt(graph_event.event, graph_event.data):
                    await self._publish(target=target, event=event)
        except SseConnectionNotFoundError:
            return
        except Exception as exc:
            await self._send_execution_error(target=target, factory=factory, exc=exc)

    async def _publish(
        self,
        *,
        target: RequestEventTarget,
        event,
    ) -> None:
        await self._sse_connection_registry.publish_agent_event(
            session_id=target.session_id,
            user_id=target.user_id,
            page_id=target.page_id,
            event=event,
        )

    async def _send_execution_error(
        self,
        *,
        target: RequestEventTarget,
        factory: AgentStreamEventFactory,
        exc: Exception,
    ) -> None:
        try:
            await self._publish(
                target=target,
                event=factory.build_error(
                    code=self._error_code(exc),
                    message=self._error_message(exc),
                    retriable=self._is_retriable(exc),
                ),
            )
        except SseConnectionNotFoundError:
            return

    @staticmethod
    def _attach_tool_event_emitter(
        *,
        request_context: GraphRequestContext | None,
        tool_event_emitter: ToolEventEmitter,
    ) -> GraphRequestContext:
        if request_context is None:
            return GraphRequestContext(
                current_user=AuthenticatedUser.anonymous(),
                trace_id=tool_event_emitter.target.trace_id,
                request_headers={},
                tool_event_emitter=tool_event_emitter,
            )
        return replace(request_context, tool_event_emitter=tool_event_emitter)

    @staticmethod
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

    @staticmethod
    def _error_message(exc: Exception) -> str:
        if isinstance(exc, GraphPayloadValidationError):
            return str(exc.errors)
        return str(exc)

    @staticmethod
    def _is_retriable(exc: Exception) -> bool:
        return not isinstance(exc, GraphPayloadValidationError)

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
