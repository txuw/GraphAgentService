import asyncio
import json

from langchain_core.messages import AIMessageChunk, ToolMessage, message_to_dict

from overmindagent.services import (
    ChatStreamService,
    GraphStreamEvent,
    SseConnectionNotFoundError,
    SseConnectionRegistry,
    SseEventAdapter,
)


class FakeGraphService:
    async def stream_events(self, graph_name: str, payload: dict[str, object]):
        yield GraphStreamEvent(
            event="session",
            data={"graph_name": graph_name, "session_id": payload["session_id"]},
        )
        yield GraphStreamEvent(
            event="updates",
            data={"ns": ["prepare"], "data": {"prepare": {"query": "hello"}}},
        )
        yield GraphStreamEvent(
            event="messages",
            data={
                "message": message_to_dict(
                    AIMessageChunk(
                        content="",
                        tool_call_chunks=[
                            {
                                "name": "lookup_weather",
                                "args": '{"location":"Shanghai"}',
                                "id": "call_1",
                                "index": 0,
                                "type": "tool_call_chunk",
                            }
                        ],
                    )
                ),
                "metadata": {},
            },
        )
        yield GraphStreamEvent(
            event="messages",
            data={
                "message": message_to_dict(
                    ToolMessage(
                        content="Shanghai weather is cloudy, 25C.",
                        tool_call_id="call_1",
                    )
                ),
                "metadata": {},
            },
        )
        yield GraphStreamEvent(
            event="messages",
            data={
                "message": message_to_dict(AIMessageChunk(content="最终回答")),
                "metadata": {},
            },
        )
        yield GraphStreamEvent(event="result", data={"answer": "最终回答"})
        yield GraphStreamEvent(event="completed", data={"session_id": payload["session_id"]})


def test_chat_stream_service_pushes_process_and_ai_events() -> None:
    async def scenario() -> None:
        registry = SseConnectionRegistry(heartbeat_interval=60.0)
        connection = await registry.register(session_id="session-1", page_id="page-1")
        service = ChatStreamService(FakeGraphService(), registry)

        accepted = await service.execute(
            graph_name="tool-agent",
            payload={"query": "上海天气"},
            session_id="session-1",
            page_id="page-1",
            request_id="request-1",
        )

        assert accepted.request_id == "request-1"

        first = await asyncio.wait_for(connection.queue.get(), timeout=1)
        second = await asyncio.wait_for(connection.queue.get(), timeout=1)
        third = await asyncio.wait_for(connection.queue.get(), timeout=1)
        fourth = await asyncio.wait_for(connection.queue.get(), timeout=1)

        assert first is not None
        assert second is not None
        assert third is not None
        assert fourth is not None

        first_payload = json.loads(first.data)
        second_payload = json.loads(second.data)
        third_payload = json.loads(third.data)
        fourth_payload = json.loads(fourth.data)

        assert first.event == "process"
        assert first_payload["code"] == "GRAPH_NODE_UPDATED"
        assert first_payload["request_id"] == "request-1"

        assert second.event == "process"
        assert second_payload["code"] == "TOOL_CALLING"

        assert third.event == "process"
        assert third_payload["code"] == "TOOL_RESULT"

        assert fourth.event == "ai_token"
        assert fourth_payload["content"] == "最终回答"

        fifth = await asyncio.wait_for(connection.queue.get(), timeout=1)
        assert fifth is not None
        fifth_payload = json.loads(fifth.data)
        assert fifth.event == "ai_done"
        assert fifth_payload["status"] == "completed"

    asyncio.run(scenario())


def test_sse_event_adapter_uses_result_as_text_fallback() -> None:
    adapter = SseEventAdapter(
        graph_name="text-analysis",
        session_id="session-1",
        page_id="page-1",
        request_id="request-1",
    )

    events = adapter.adapt(
        GraphStreamEvent(
            event="result",
            data={
                "normalized_text": "hello world",
                "analysis": {"summary": "Greeting summary"},
            },
        )
    )

    assert len(events) == 1
    assert events[0].event == "ai_token"
    assert events[0].payload["content"] == "Greeting summary"


def test_chat_stream_service_requires_existing_connection() -> None:
    async def scenario() -> None:
        registry = SseConnectionRegistry()
        service = ChatStreamService(FakeGraphService(), registry)

        try:
            await service.execute(
                graph_name="tool-agent",
                payload={"query": "上海天气"},
                session_id="missing-session",
                page_id="missing-page",
            )
        except SseConnectionNotFoundError:
            return

        raise AssertionError("expected missing connection error")

    asyncio.run(scenario())
