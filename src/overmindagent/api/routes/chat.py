from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from overmindagent.api.dependencies import (
    get_chat_stream_service,
    get_sse_connection_registry,
)
from overmindagent.schemas.api import ChatExecuteRequest, ChatExecuteResponse
from overmindagent.services.chat_stream_service import ChatStreamService
from overmindagent.services.sse import (
    SseConnectionNotFoundError,
    SseConnectionRegistry,
)

router = APIRouter(tags=["chat"])


@router.get("/sse/connect")
async def connect_sse(
    request: Request,
    session_id: str | None = Query(default=None, alias="sessionId"),
    page_id: str | None = Query(default=None, alias="pageId"),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    sse_connection_registry: SseConnectionRegistry = Depends(get_sse_connection_registry),
) -> StreamingResponse:
    resolved_session_id = _resolve_optional_id(session_id)
    resolved_page_id = _resolve_optional_id(page_id)
    connection = await sse_connection_registry.register(
        session_id=resolved_session_id,
        page_id=resolved_page_id,
        last_event_id=last_event_id,
    )
    await sse_connection_registry.send_connected_event(connection)

    async def event_generator():
        async for chunk in sse_connection_registry.event_stream(
            connection,
            is_disconnected=request.is_disconnected,
        ):
            yield chunk

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/chat/execute", response_model=ChatExecuteResponse)
async def execute_chat(
    body: ChatExecuteRequest,
    chat_stream_service: ChatStreamService = Depends(get_chat_stream_service),
) -> ChatExecuteResponse:
    try:
        accepted = await chat_stream_service.execute(
            graph_name=body.graph_name,
            payload=body.input,
            session_id=body.session_id,
            page_id=body.page_id,
            request_id=body.request_id,
        )
    except SseConnectionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return ChatExecuteResponse(
        graph_name=accepted.graph_name,
        session_id=accepted.session_id,
        page_id=accepted.page_id,
        request_id=accepted.request_id,
    )


def _resolve_optional_id(value: str | None) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return uuid4().hex
