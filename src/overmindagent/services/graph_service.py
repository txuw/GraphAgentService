from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import uuid4

from overmindagent.graphs.registry import GraphRegistry
from overmindagent.llm import LLMEvent, LLMEventType
from overmindagent.schemas.analysis import TextAnalysisOutput, TextAnalysisRequest


@dataclass(slots=True)
class GraphInvocationResult:
    graph_name: str
    session_id: str
    output: TextAnalysisOutput


class GraphService:
    def __init__(self, registry: GraphRegistry) -> None:
        self._registry = registry

    async def invoke(
        self,
        graph_name: str,
        payload: TextAnalysisRequest,
    ) -> GraphInvocationResult:
        runtime = self._registry.get(graph_name)
        session_id = payload.session_id or uuid4().hex
        state = await runtime.graph.ainvoke(
            {"text": payload.text},
            config={"configurable": {"thread_id": session_id}},
        )
        return GraphInvocationResult(
            graph_name=graph_name,
            session_id=session_id,
            output=TextAnalysisOutput.model_validate(state["output"]),
        )

    async def stream(
        self,
        graph_name: str,
        payload: TextAnalysisRequest,
    ) -> AsyncIterator[str]:
        runtime = self._registry.get(graph_name)
        session_id = payload.session_id or uuid4().hex

        preprocessed = runtime.nodes.preprocess({"text": payload.text})
        normalized_text = preprocessed["normalized_text"]
        yield self._to_sse("session", {"graph_name": graph_name, "session_id": session_id})

        if not normalized_text:
            output = runtime.nodes.finalize(runtime.nodes.empty(preprocessed))["output"]
            yield self._to_sse("result", output.model_dump())
            yield self._to_sse("completed", {"session_id": session_id})
            return

        text_chunks: list[str] = []
        final_text: str | None = None
        async for event in runtime.nodes.stream_analysis(normalized_text):
            if event.text_delta:
                text_chunks.append(event.text_delta)
            if event.type == LLMEventType.COMPLETED and event.raw is not None:
                # Responses streaming exposes the final payload here, which keeps structured parsing single-pass.
                final_text = getattr(event.raw, "output_text", None)
            yield self._to_sse(event.type.value, self._event_payload(event))

        analysis = runtime.nodes.parse_analysis_text(final_text or "".join(text_chunks))
        output = runtime.nodes.finalize(
            {"normalized_text": normalized_text, "analysis": analysis}
        )["output"]
        yield self._to_sse("result", output.model_dump())
        yield self._to_sse("completed", {"session_id": session_id})

    @staticmethod
    def _event_payload(event: LLMEvent) -> dict[str, object]:
        payload: dict[str, object] = {"type": event.type.value}
        if event.text_delta is not None:
            payload["text_delta"] = event.text_delta
        if event.tool_call is not None:
            payload["tool_call"] = event.tool_call.model_dump()
        if event.tool_result is not None:
            payload["tool_result"] = event.tool_result.model_dump()
        if event.usage:
            payload["usage"] = event.usage
        if event.type == LLMEventType.ERROR:
            payload["error"] = event.extensions.get("message", "unknown error")
        return payload

    @staticmethod
    def _to_sse(event: str, data: dict[str, object]) -> str:
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=True)}\n\n"
