from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import uuid4

from fastapi.encoders import jsonable_encoder
from langchain_core.messages import message_to_dict
from pydantic import BaseModel, ValidationError

from graphagentservice.common.auth import AuthenticatedUser
from graphagentservice.common.trace import TRACE_ID_HEADER, resolve_request_trace_context
from graphagentservice.graphs.registry import GraphRegistry
from graphagentservice.graphs.runtime import GraphRunContext, GraphRuntime
from graphagentservice.llm.router import LLMRouter
from graphagentservice.mcp import MCPToolResolver


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


class GraphPayloadValidationError(ValueError):
    def __init__(self, *, graph_name: str, errors: list[dict[str, object]]) -> None:
        super().__init__(f"Invalid payload for graph: {graph_name}")
        self.graph_name = graph_name
        self.errors = errors


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
        state = await runtime.graph.ainvoke(
            graph_input.model_dump(),
            config=self._build_graph_config(
                runtime=runtime,
                session_id=resolved_session_id,
            ),
            context=self._build_context(runtime, request_context=request_context),
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
