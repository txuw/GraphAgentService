from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import uuid4

from fastapi.encoders import jsonable_encoder
from langchain_core.messages import message_to_dict
from pydantic import BaseModel, ValidationError

from overmindagent.graphs.registry import GraphRegistry
from overmindagent.graphs.runtime import GraphRunContext, GraphRuntime
from overmindagent.llm.router import LLMRouter


@dataclass(slots=True)
class GraphInvocationResult:
    graph_name: str
    session_id: str
    output: BaseModel


@dataclass(slots=True)
class GraphStreamEvent:
    event: str
    data: dict[str, object]


class GraphPayloadValidationError(ValueError):
    def __init__(self, *, graph_name: str, errors: list[dict[str, object]]) -> None:
        super().__init__(f"Invalid payload for graph: {graph_name}")
        self.graph_name = graph_name
        self.errors = errors


class GraphService:
    def __init__(self, registry: GraphRegistry, llm_router: LLMRouter) -> None:
        self._registry = registry
        self._llm_router = llm_router

    def list_graphs(self) -> tuple[GraphRuntime, ...]:
        return self._registry.list_runtimes()

    async def invoke(
        self,
        graph_name: str,
        payload: dict[str, object],
    ) -> GraphInvocationResult:
        runtime = self._registry.get(graph_name)
        graph_input = self._validate_payload(runtime, payload)
        session_id = self._resolve_session_id(payload)
        state = await runtime.graph.ainvoke(
            graph_input.model_dump(),
            config={"configurable": {"thread_id": session_id}},
            context=self._build_context(runtime),
        )
        return GraphInvocationResult(
            graph_name=graph_name,
            session_id=session_id,
            output=runtime.output_model.model_validate(state),
        )

    async def stream(
        self,
        graph_name: str,
        payload: dict[str, object],
    ) -> AsyncIterator[str]:
        async for event in self.stream_events(graph_name=graph_name, payload=payload):
            yield self._to_sse(event.event, event.data)

    async def stream_events(
        self,
        graph_name: str,
        payload: dict[str, object],
    ) -> AsyncIterator[GraphStreamEvent]:
        runtime = self._registry.get(graph_name)
        graph_input = self._validate_payload(runtime, payload)
        session_id = self._resolve_session_id(payload)

        yield GraphStreamEvent(
            event="session",
            data={"graph_name": graph_name, "session_id": session_id},
        )
        final_state: dict[str, object] | None = None
        async for chunk in runtime.graph.astream(
            graph_input.model_dump(),
            config={"configurable": {"thread_id": session_id}},
            context=self._build_context(runtime),
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
        yield GraphStreamEvent(event="completed", data={"session_id": session_id})

    @staticmethod
    def _resolve_session_id(payload: dict[str, object]) -> str:
        session_id = payload.get("session_id")
        if isinstance(session_id, str) and session_id:
            return session_id
        return uuid4().hex

    def _build_context(self, runtime: GraphRuntime) -> GraphRunContext:
        return GraphRunContext(
            llm_router=self._llm_router,
            graph_name=runtime.name,
            llm_bindings=runtime.llm_bindings,
        )

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
