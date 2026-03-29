from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import uuid4

from fastapi.encoders import jsonable_encoder
from langchain_core.messages import message_to_dict
from pydantic import BaseModel, ValidationError

from graphagentservice.common.auth import AuthenticatedUser
from graphagentservice.common.trace import TRACE_ID_HEADER, resolve_request_trace_context
from graphagentservice.graphs.registry import GraphRegistry
from graphagentservice.graphs.runtime import GraphRunContext, GraphRuntime, ToolEventEmitterProtocol
from graphagentservice.llm.router import LLMRouter
from graphagentservice.mcp import MCPToolResolver

_logger = logging.getLogger(__name__)


@dataclass(slots=True)
class GraphInvocationResult:
    graph_name: str
    session_id: str
    output: BaseModel


@dataclass(slots=True)
class GraphStreamEvent:
    event: str
    data: dict[str, object]


@dataclass(frozen=True, slots=True)
class GraphRequestContext:
    current_user: AuthenticatedUser
    trace_id: str
    request_headers: dict[str, str]
    tool_stream_emitter: ToolEventEmitterProtocol | None = None


class GraphPayloadValidationError(ValueError):
    def __init__(self, *, graph_name: str, errors: list[dict[str, object]]) -> None:
        super().__init__(f"Invalid payload for graph: {graph_name}")
        self.graph_name = graph_name
        self.errors = errors


class GraphCheckpointUnavailableError(RuntimeError):
    pass


class GraphStateNotFoundError(LookupError):
    def __init__(self, *, graph_name: str, session_id: str) -> None:
        super().__init__(f"No checkpoint state found for graph={graph_name} session_id={session_id}")
        self.graph_name = graph_name
        self.session_id = session_id


class _SessionExecutionCoordinator:
    def __init__(self) -> None:
        self._registry_lock = asyncio.Lock()
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._references: dict[tuple[str, str], int] = {}

    @asynccontextmanager
    async def hold(self, *, graph_name: str, session_id: str):
        key = (graph_name, session_id)
        lock = await self._retain_lock(key)
        try:
            await lock.acquire()
        except BaseException:
            await self._release_reference(key, lock)
            raise
        try:
            yield
        finally:
            lock.release()
            await self._release_reference(key, lock)

    async def _retain_lock(self, key: tuple[str, str]) -> asyncio.Lock:
        async with self._registry_lock:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
                self._references[key] = 0
            self._references[key] += 1
            return lock

    async def _release_reference(
        self,
        key: tuple[str, str],
        lock: asyncio.Lock,
    ) -> None:
        async with self._registry_lock:
            remaining = self._references.get(key, 0) - 1
            if remaining > 0:
                self._references[key] = remaining
                return
            self._references.pop(key, None)
            if not lock.locked():
                self._locks.pop(key, None)


class GraphService:
    def __init__(
        self,
        registry: GraphRegistry,
        llm_router: LLMRouter,
        mcp_tool_resolver: MCPToolResolver | None = None,
        checkpoint_namespace_prefix: str = "",
    ) -> None:
        self._registry = registry
        self._llm_router = llm_router
        self._mcp_tool_resolver = mcp_tool_resolver
        self._checkpoint_namespace_prefix = checkpoint_namespace_prefix.strip()
        self._session_execution_coordinator = _SessionExecutionCoordinator()

    def list_graphs(self) -> tuple[GraphRuntime, ...]:
        return self._registry.list_runtimes()

    async def invoke(
        self,
        graph_name: str,
        payload: BaseModel | dict[str, object],
        session_id: str | None = None,
        request_context: GraphRequestContext | None = None,
    ) -> GraphInvocationResult:
        runtime = self._registry.get(graph_name)
        payload_dict = self._payload_to_dict(payload)
        graph_input = self._validate_payload(runtime, payload_dict)
        resolved_session_id = self._resolve_session_id(session_id=session_id, payload=payload_dict)
        thread_id = self._build_thread_id(graph_name=runtime.name, session_id=resolved_session_id)
        _logger.info(
            "Graph invoke started  graph=%s  session=%s  threadId=%s",
            graph_name,
            resolved_session_id,
            thread_id,
        )
        t0 = time.perf_counter()
        try:
            async with self._session_execution_coordinator.hold(
                graph_name=runtime.name,
                session_id=resolved_session_id,
            ):
                state = await runtime.graph.ainvoke(
                    graph_input.model_dump(),
                    config=self._build_graph_config(
                        runtime=runtime,
                        session_id=resolved_session_id,
                    ),
                    context=self._build_context(runtime, request_context=request_context),
                )
        except Exception:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            _logger.exception(
                "Graph invoke failed  graph=%s  session=%s  elapsed=%.0fms",
                graph_name,
                resolved_session_id,
                elapsed_ms,
            )
            raise
        elapsed_ms = (time.perf_counter() - t0) * 1000
        _logger.info(
            "Graph invoke completed  graph=%s  session=%s  elapsed=%.0fms",
            graph_name,
            resolved_session_id,
            elapsed_ms,
        )
        return GraphInvocationResult(
            graph_name=graph_name,
            session_id=resolved_session_id,
            output=runtime.output_model.model_validate(state),
        )

    async def stream(
        self,
        graph_name: str,
        payload: BaseModel | dict[str, object],
        session_id: str | None = None,
        request_context: GraphRequestContext | None = None,
    ) -> AsyncIterator[str]:
        async for event in self.stream_events(
            graph_name=graph_name,
            payload=payload,
            session_id=session_id,
            request_context=request_context,
        ):
            yield self._to_sse(event.event, event.data)

    async def stream_events(
        self,
        graph_name: str,
        payload: BaseModel | dict[str, object],
        session_id: str | None = None,
        request_context: GraphRequestContext | None = None,
    ) -> AsyncIterator[GraphStreamEvent]:
        runtime = self._registry.get(graph_name)
        payload_dict = self._payload_to_dict(payload)
        graph_input = self._validate_payload(runtime, payload_dict)
        resolved_session_id = self._resolve_session_id(session_id=session_id, payload=payload_dict)
        thread_id = self._build_thread_id(graph_name=runtime.name, session_id=resolved_session_id)
        _logger.info(
            "Graph stream started  graph=%s  session=%s  threadId=%s",
            graph_name,
            resolved_session_id,
            thread_id,
        )
        t0 = time.perf_counter()
        chunk_count = 0
        async with self._session_execution_coordinator.hold(
            graph_name=runtime.name,
            session_id=resolved_session_id,
        ):
            yield GraphStreamEvent(
                event="session",
                data={"graph_name": graph_name, "session_id": resolved_session_id},
            )
            final_state: dict[str, object] | None = None
            async for chunk in runtime.graph.astream(
                graph_input.model_dump(),
                config=self._build_graph_config(
                    runtime=runtime,
                    session_id=resolved_session_id,
                ),
                context=self._build_context(runtime, request_context=request_context),
                stream_mode=list(runtime.stream_modes),
                version="v2",
            ):
                chunk_count += 1
                chunk_type = str(chunk["type"])
                if chunk_type == "values":
                    final_state = chunk["data"]
                    continue
                yield GraphStreamEvent(
                    event=chunk_type,
                    data=self._serialize_stream_chunk(chunk),
                )

            output = runtime.output_model.model_validate(final_state or {})
            yield GraphStreamEvent(event="result", data=output.model_dump())
            yield GraphStreamEvent(event="completed", data={"session_id": resolved_session_id})

        elapsed_ms = (time.perf_counter() - t0) * 1000
        _logger.info(
            "Graph stream completed  graph=%s  session=%s  chunks=%d  elapsed=%.0fms",
            graph_name,
            resolved_session_id,
            chunk_count,
            elapsed_ms,
        )

    async def get_latest_state(
        self,
        graph_name: str,
        *,
        session_id: str,
    ) -> dict[str, object]:
        runtime = self._registry.get(graph_name)
        resolved_session_id = self._resolve_session_id(
            session_id=session_id,
            payload={},
        )
        _logger.debug("Get latest state  graph=%s  session=%s", graph_name, resolved_session_id)
        async with self._session_execution_coordinator.hold(
            graph_name=runtime.name,
            session_id=resolved_session_id,
        ):
            try:
                snapshot = await runtime.graph.aget_state(
                    {
                        "configurable": {
                            "thread_id": self._build_thread_id(
                                graph_name=runtime.name,
                                session_id=resolved_session_id,
                            )
                        }
                    }
                )
            except ValueError as exc:
                if "No checkpointer set" in str(exc):
                    raise GraphCheckpointUnavailableError(
                        f"Checkpoint is not enabled for graph: {graph_name}"
                    ) from exc
                raise

        values = snapshot.values if isinstance(snapshot.values, dict) else {}
        if not values and snapshot.metadata is None:
            raise GraphStateNotFoundError(
                graph_name=runtime.name,
                session_id=resolved_session_id,
            )
        return {str(key): value for key, value in values.items()}

    @staticmethod
    def _resolve_session_id(*, session_id: str | None, payload: dict[str, object]) -> str:
        if isinstance(session_id, str) and session_id:
            return session_id
        payload_session_id = payload.get("session_id")
        if isinstance(payload_session_id, str) and payload_session_id:
            return payload_session_id
        return uuid4().hex

    def _build_context(
        self,
        runtime: GraphRuntime,
        *,
        request_context: GraphRequestContext | None = None,
    ) -> GraphRunContext:
        if request_context is None:
            trace_context = resolve_request_trace_context({})
            resolved_request_context = GraphRequestContext(
                current_user=AuthenticatedUser.anonymous(),
                trace_id=trace_context.trace_id,
                request_headers=trace_context.request_headers,
            )
        else:
            request_headers = dict(request_context.request_headers)
            if request_context.trace_id:
                request_headers[TRACE_ID_HEADER] = request_context.trace_id
            trace_context = resolve_request_trace_context(request_headers)
            resolved_request_context = GraphRequestContext(
                current_user=request_context.current_user,
                trace_id=trace_context.trace_id,
                request_headers=trace_context.request_headers,
            )
        return GraphRunContext(
            llm_router=self._llm_router,
            graph_name=runtime.name,
            llm_bindings=runtime.llm_bindings,
            current_user=resolved_request_context.current_user,
            trace_id=resolved_request_context.trace_id,
            request_headers=resolved_request_context.request_headers,
            mcp_tool_resolver=self._mcp_tool_resolver,
            mcp_servers=runtime.mcp_servers,
            tool_stream_emitter=request_context.tool_stream_emitter
            if request_context is not None
            else None,
        )

    def _build_graph_config(
        self,
        *,
        runtime: GraphRuntime,
        session_id: str,
    ) -> dict[str, dict[str, str]]:
        return {
            "configurable": {
                "thread_id": self._build_thread_id(
                    graph_name=runtime.name,
                    session_id=session_id,
                ),
                "checkpoint_ns": self._build_checkpoint_namespace(runtime.name),
            }
        }

    def _build_thread_id(self, *, graph_name: str, session_id: str) -> str:
        if not self._checkpoint_namespace_prefix:
            return f"{graph_name}:{session_id}"
        return f"{self._checkpoint_namespace_prefix}:{graph_name}:{session_id}"

    def _build_checkpoint_namespace(self, graph_name: str) -> str:
        if not self._checkpoint_namespace_prefix:
            return graph_name
        return f"{self._checkpoint_namespace_prefix}:{graph_name}"

    @staticmethod
    def _validate_payload(runtime: GraphRuntime, payload: dict[str, object]) -> BaseModel:
        request_payload = dict(payload)
        request_payload.pop("session_id", None)
        try:
            return runtime.input_model.model_validate(request_payload)
        except ValidationError as exc:
            raise GraphPayloadValidationError(
                graph_name=runtime.name,
                errors=exc.errors(),
            ) from exc

    @staticmethod
    def _payload_to_dict(payload: BaseModel | dict[str, object]) -> dict[str, object]:
        if isinstance(payload, BaseModel):
            serialized = payload.model_dump()
        else:
            serialized = dict(payload)
        return {str(key): value for key, value in serialized.items()}

    @staticmethod
    def _serialize_stream_chunk(chunk: dict[str, object]) -> dict[str, object]:
        chunk_type = str(chunk["type"])
        namespace = list(chunk.get("ns", ()))
        if chunk_type == "messages":
            message_chunk, metadata = chunk["data"]
            return {
                "ns": namespace,
                "message": message_to_dict(message_chunk),
                "metadata": jsonable_encoder(metadata),
            }

        payload: dict[str, object] = {
            "ns": namespace,
            "data": jsonable_encoder(chunk.get("data")),
        }
        interrupts = chunk.get("interrupts")
        if interrupts:
            payload["interrupts"] = jsonable_encoder(interrupts)
        return payload

    @staticmethod
    def _to_sse(event: str, data: dict[str, object]) -> str:
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=True)}\n\n"
