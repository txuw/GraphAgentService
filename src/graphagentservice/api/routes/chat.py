from __future__ import annotations

import logging
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from graphagentservice.api.dependencies import (
    build_graph_request_context,
    get_chat_stream_service,
    get_current_user,
    get_sse_connection_registry,
)
from graphagentservice.common.trace import build_trace_response_headers

_logger = logging.getLogger(__name__)
from graphagentservice.schemas.api import (
    ChatExecuteRequest,
    ChatExecuteRequestBase,
    ChatExecuteResponse,
    ImageAgentChatExecuteRequest,
    ImageCaloriesChatExecuteRequest,
    PlanAnalyzeChatExecuteRequest,
    TextAnalysisChatExecuteRequest,
    ToolAgentChatExecuteRequest,
)
from graphagentservice.services.chat_stream_service import ChatStreamService
from graphagentservice.services.sse import (
    SseConnectionNotFoundError,
    SseConnectionRegistry,
)

router = APIRouter(tags=["chat"])

TEXT_ANALYSIS_GRAPH = "text-analysis"
PLAN_ANALYZE_GRAPH = "plan-analyze"
TOOL_AGENT_GRAPH = "tool-agent"
IMAGE_AGENT_GRAPH = "image-agent"
IMAGE_ANALYZE_CALORIES_GRAPH = "image-analyze-calories"


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
    current_user = get_current_user(request)
    _logger.info(
        "SSE connect  session=%s  page=%s  user=%s  lastEventId=%s",
        resolved_session_id,
        resolved_page_id,
        current_user.user_id or "anonymous",
        last_event_id or "-",
    )
    connection = await sse_connection_registry.register(
        session_id=resolved_session_id,
        page_id=resolved_page_id,
        user_id=current_user.user_id,
        last_event_id=last_event_id,
    )
    await sse_connection_registry.send_connected_event(connection)
    _logger.debug("SSE connection registered  session=%s  page=%s", resolved_session_id, resolved_page_id)

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


@router.post(
    "/chat/text-analysis/execute",
    response_model=ChatExecuteResponse,
    operation_id="executeTextAnalysisChat",
)
async def execute_text_analysis_chat(
    request: Request,
    response: Response,
    body: TextAnalysisChatExecuteRequest,
    chat_stream_service: ChatStreamService = Depends(get_chat_stream_service),
) -> ChatExecuteResponse:
    return await _execute_chat(
        request=request,
        response=response,
        graph_name=TEXT_ANALYSIS_GRAPH,
        body=body,
        chat_stream_service=chat_stream_service,
    )


@router.post(
    "/chat/plan-analyze/execute",
    response_model=ChatExecuteResponse,
    operation_id="executePlanAnalyzeChat",
)
async def execute_plan_analyze_chat(
    request: Request,
    response: Response,
    body: PlanAnalyzeChatExecuteRequest,
    chat_stream_service: ChatStreamService = Depends(get_chat_stream_service),
) -> ChatExecuteResponse:
    return await _execute_chat(
        request=request,
        response=response,
        graph_name=PLAN_ANALYZE_GRAPH,
        body=body,
        chat_stream_service=chat_stream_service,
    )


@router.post(
    "/chat/tool-agent/execute",
    response_model=ChatExecuteResponse,
    operation_id="executeToolAgentChat",
)
async def execute_tool_agent_chat(
    request: Request,
    response: Response,
    body: ToolAgentChatExecuteRequest,
    chat_stream_service: ChatStreamService = Depends(get_chat_stream_service),
) -> ChatExecuteResponse:
    return await _execute_chat(
        request=request,
        response=response,
        graph_name=TOOL_AGENT_GRAPH,
        body=body,
        chat_stream_service=chat_stream_service,
    )


@router.post(
    "/chat/image-agent/execute",
    response_model=ChatExecuteResponse,
    operation_id="executeImageAgentChat",
)
async def execute_image_agent_chat(
    request: Request,
    response: Response,
    body: ImageAgentChatExecuteRequest,
    chat_stream_service: ChatStreamService = Depends(get_chat_stream_service),
) -> ChatExecuteResponse:
    return await _execute_chat(
        request=request,
        response=response,
        graph_name=IMAGE_AGENT_GRAPH,
        body=body,
        chat_stream_service=chat_stream_service,
    )


@router.post(
    "/chat/image-analyze-calories/execute",
    response_model=ChatExecuteResponse,
    operation_id="executeImageAnalyzeCaloriesChat",
)
async def execute_image_analyze_calories_chat(
    request: Request,
    response: Response,
    body: ImageCaloriesChatExecuteRequest,
    chat_stream_service: ChatStreamService = Depends(get_chat_stream_service),
) -> ChatExecuteResponse:
    return await _execute_chat(
        request=request,
        response=response,
        graph_name=IMAGE_ANALYZE_CALORIES_GRAPH,
        body=body,
        chat_stream_service=chat_stream_service,
    )


@router.post(
    "/chat/execute",
    response_model=ChatExecuteResponse,
    include_in_schema=False,
)
async def execute_chat(
    request: Request,
    response: Response,
    body: ChatExecuteRequest,
    chat_stream_service: ChatStreamService = Depends(get_chat_stream_service),
) -> ChatExecuteResponse:
    request_context = build_graph_request_context(request)
    trace_headers = build_trace_response_headers(request_context.trace_id)
    _logger.info(
        "Chat execute  graph=%s  session=%s  page=%s  requestId=%s",
        body.graph_name,
        body.session_id or "-",
        body.page_id or "-",
        body.request_id or "-",
    )
    try:
        accepted = await chat_stream_service.execute(
            graph_name=body.graph_name,
            payload=body.input,
            session_id=body.session_id,
            page_id=body.page_id,
            request_id=body.request_id,
            request_context=request_context,
        )
    except SseConnectionNotFoundError as exc:
        _logger.warning(
            "Chat execute rejected – SSE connection not found  graph=%s  session=%s  error=%s",
            body.graph_name,
            body.session_id or "-",
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
            headers=trace_headers,
        ) from exc

    _logger.info(
        "Chat execute accepted  graph=%s  session=%s  page=%s  requestId=%s",
        accepted.graph_name,
        accepted.session_id,
        accepted.page_id or "-",
        accepted.request_id,
    )
    response.headers.update(trace_headers)
    return ChatExecuteResponse(
        graph_name=accepted.graph_name,
        session_id=accepted.session_id,
        page_id=accepted.page_id,
        request_id=accepted.request_id,
    )


async def _execute_chat(
    *,
    request: Request,
    response: Response,
    graph_name: str,
    body: ChatExecuteRequestBase,
    chat_stream_service: ChatStreamService,
) -> ChatExecuteResponse:
    request_context = build_graph_request_context(request)
    trace_headers = build_trace_response_headers(request_context.trace_id)
    payload = body.graph_payload()
    _logger.info(
        "Chat execute  graph=%s  session=%s  page=%s  requestId=%s",
        graph_name,
        body.session_id or "-",
        body.page_id or "-",
        body.request_id or "-",
    )
    try:
        accepted = await chat_stream_service.execute(
            graph_name=graph_name,
            payload=payload,
            session_id=body.session_id,
            page_id=body.page_id,
            request_id=body.request_id,
            request_context=request_context,
        )
    except SseConnectionNotFoundError as exc:
        _logger.warning(
            "Chat execute rejected – SSE connection not found  graph=%s  session=%s  error=%s",
            graph_name,
            body.session_id or "-",
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
            headers=trace_headers,
        ) from exc

    _logger.info(
        "Chat execute accepted  graph=%s  session=%s  page=%s  requestId=%s",
        accepted.graph_name,
        accepted.session_id,
        accepted.page_id or "-",
        accepted.request_id,
    )
    response.headers.update(trace_headers)
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
