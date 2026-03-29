from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

_logger = logging.getLogger(__name__)

from graphagentservice.api.dependencies import (
    build_graph_request_context,
    get_graph_service,
    get_graph_stream_dispatch_service,
    get_plan_analyze_summary_service,
)
from graphagentservice.common.trace import build_trace_response_headers
from graphagentservice.graphs.registry import GraphNotFoundError
from graphagentservice.llm import ChatModelBuildError
from graphagentservice.mcp import MCPConfigurationError, MCPToolResolutionError
from graphagentservice.schemas.api import (
    GraphDescriptorResponse,
    GraphInvokeResult,
    ImageAgentGraphRequest,
    ImageAgentInvokeResult,
    ImageCaloriesGraphRequest,
    ImageCaloriesInvokeResult,
    PlanAnalyzeGraphRequest,
    PlanAnalyzeInvokeResult,
    PlanAnalyzeSummaryInvokeResult,
    PlanAnalyzeSummaryRequest,
    ResultResponse,
    TextAnalysisGraphRequest,
    TextAnalysisInvokeResult,
    ToolAgentGraphRequest,
    ToolAgentInvokeResult,
)
from graphagentservice.services.graph_service import (
    GraphCheckpointUnavailableError,
    GraphPayloadValidationError,
    GraphService,
    GraphStateNotFoundError,
)
from graphagentservice.services.graph_stream_service import (
    GraphStreamDispatchService,
    graph_stream_payload_from_input,
)
from graphagentservice.services.plan_analyze_summary_service import (
    PlanAnalyzeSummaryService,
    PlanAnalyzeSummaryStateError,
)
from graphagentservice.services.sse import SseConnectionNotFoundError

router = APIRouter(tags=["graphs"])

TEXT_ANALYSIS_GRAPH = "text-analysis"
PLAN_ANALYZE_GRAPH = "plan-analyze"
TOOL_AGENT_GRAPH = "tool-agent"
IMAGE_AGENT_GRAPH = "image-agent"
IMAGE_ANALYZE_CALORIES_GRAPH = "image-analyze-calories"


@router.get("/graphs", response_model=list[GraphDescriptorResponse])
async def list_graphs(
    graph_service: GraphService = Depends(get_graph_service),
) -> list[GraphDescriptorResponse]:
    return [
        GraphDescriptorResponse(
            name=runtime.name,
            description=runtime.description,
            input_schema=runtime.input_model.model_json_schema(),
            output_schema=runtime.output_model.model_json_schema(),
            stream_modes=list(runtime.stream_modes),
        )
        for runtime in graph_service.list_graphs()
    ]


@router.post(
    "/graphs/text-analysis/invoke",
    response_model=TextAnalysisInvokeResult,
    operation_id="invokeTextAnalysisGraph",
)
async def invoke_text_analysis_graph(
    request: Request,
    response: Response,
    body: TextAnalysisGraphRequest,
    session_id: str | None = Query(default=None, alias="sessionId"),
    graph_service: GraphService = Depends(get_graph_service),
) -> ResultResponse[dict[str, Any]]:
    return await _invoke_graph(
        request=request,
        response=response,
        graph_name=TEXT_ANALYSIS_GRAPH,
        payload=body.graph_payload(),
        session_id=_resolve_identifier(body.session_id, session_id),
        graph_service=graph_service,
    )


@router.post(
    "/graphs/plan-analyze/invoke",
    response_model=PlanAnalyzeInvokeResult,
    operation_id="invokePlanAnalyzeGraph",
)
async def invoke_plan_analyze_graph(
    request: Request,
    response: Response,
    body: PlanAnalyzeGraphRequest,
    session_id: str | None = Query(default=None, alias="sessionId"),
    graph_service: GraphService = Depends(get_graph_service),
) -> ResultResponse[dict[str, Any]]:
    return await _invoke_graph(
        request=request,
        response=response,
        graph_name=PLAN_ANALYZE_GRAPH,
        payload=body.graph_payload(),
        session_id=_resolve_identifier(body.session_id, session_id),
        graph_service=graph_service,
    )


@router.post(
    "/graphs/plan-analyze/summary/invoke",
    response_model=PlanAnalyzeSummaryInvokeResult,
    operation_id="invokePlanAnalyzeSummary",
)
async def invoke_plan_analyze_summary(
    request: Request,
    response: Response,
    body: PlanAnalyzeSummaryRequest,
    session_id: str | None = Query(default=None, alias="sessionId"),
    plan_analyze_summary_service: PlanAnalyzeSummaryService = Depends(
        get_plan_analyze_summary_service
    ),
) -> ResultResponse[dict[str, Any]]:
    request_context = build_graph_request_context(request)
    trace_headers = build_trace_response_headers(request_context.trace_id)
    try:
        result = await plan_analyze_summary_service.summarize(
            session_id=_require_non_empty_id(
                _resolve_identifier(body.session_id, session_id),
                field_name="sessionId",
                headers=trace_headers,
            ),
            request_context=request_context,
        )
    except GraphStateNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
            headers=trace_headers,
        ) from exc
    except GraphCheckpointUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
            headers=trace_headers,
        ) from exc
    except PlanAnalyzeSummaryStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
            headers=trace_headers,
        ) from exc
    except ChatModelBuildError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
            headers=trace_headers,
        ) from exc

    response.headers.update(trace_headers)
    return ResultResponse(data=result)


@router.post(
    "/graphs/tool-agent/invoke",
    response_model=ToolAgentInvokeResult,
    operation_id="invokeToolAgentGraph",
)
async def invoke_tool_agent_graph(
    request: Request,
    response: Response,
    body: ToolAgentGraphRequest,
    session_id: str | None = Query(default=None, alias="sessionId"),
    graph_service: GraphService = Depends(get_graph_service),
) -> ResultResponse[dict[str, Any]]:
    return await _invoke_graph(
        request=request,
        response=response,
        graph_name=TOOL_AGENT_GRAPH,
        payload=body.graph_payload(),
        session_id=_resolve_identifier(body.session_id, session_id),
        graph_service=graph_service,
    )


@router.post(
    "/graphs/image-agent/invoke",
    response_model=ImageAgentInvokeResult,
    operation_id="invokeImageAgentGraph",
)
async def invoke_image_agent_graph(
    request: Request,
    response: Response,
    body: ImageAgentGraphRequest,
    session_id: str | None = Query(default=None, alias="sessionId"),
    graph_service: GraphService = Depends(get_graph_service),
) -> ResultResponse[dict[str, Any]]:
    return await _invoke_graph(
        request=request,
        response=response,
        graph_name=IMAGE_AGENT_GRAPH,
        payload=body.graph_payload(),
        session_id=_resolve_identifier(body.session_id, session_id),
        graph_service=graph_service,
    )


@router.post(
    "/graphs/image-analyze-calories/invoke",
    response_model=ImageCaloriesInvokeResult,
    operation_id="invokeImageAnalyzeCaloriesGraph",
)
async def invoke_image_analyze_calories_graph(
    request: Request,
    response: Response,
    body: ImageCaloriesGraphRequest,
    session_id: str | None = Query(default=None, alias="sessionId"),
    graph_service: GraphService = Depends(get_graph_service),
) -> ResultResponse[dict[str, Any]]:
    return await _invoke_graph(
        request=request,
        response=response,
        graph_name=IMAGE_ANALYZE_CALORIES_GRAPH,
        payload=body.graph_payload(),
        session_id=_resolve_identifier(body.session_id, session_id),
        graph_service=graph_service,
    )


@router.post(
    "/graphs/text-analysis/stream",
    response_model=ResultResponse[str],
    operation_id="streamTextAnalysisGraph",
)
async def stream_text_analysis_graph(
    request: Request,
    response: Response,
    body: TextAnalysisGraphRequest,
    session_id: str | None = Query(default=None, alias="sessionId"),
    page_id: str | None = Query(default=None, alias="pageId"),
    request_id: str | None = Query(default=None, alias="requestId"),
    graph_stream_dispatch_service: GraphStreamDispatchService = Depends(
        get_graph_stream_dispatch_service
    ),
) -> ResultResponse[str]:
    return await _dispatch_graph_stream(
        request=request,
        response=response,
        graph_name=TEXT_ANALYSIS_GRAPH,
        payload=body.graph_payload(),
        session_id=_resolve_identifier(body.session_id, session_id),
        page_id=_resolve_identifier(body.page_id, page_id),
        request_id=_resolve_identifier(body.request_id, request_id),
        graph_stream_dispatch_service=graph_stream_dispatch_service,
    )


@router.post(
    "/graphs/plan-analyze/stream",
    response_model=ResultResponse[str],
    operation_id="streamPlanAnalyzeGraph",
)
async def stream_plan_analyze_graph(
    request: Request,
    response: Response,
    body: PlanAnalyzeGraphRequest,
    session_id: str | None = Query(default=None, alias="sessionId"),
    page_id: str | None = Query(default=None, alias="pageId"),
    request_id: str | None = Query(default=None, alias="requestId"),
    graph_stream_dispatch_service: GraphStreamDispatchService = Depends(
        get_graph_stream_dispatch_service
    ),
) -> ResultResponse[str]:
    return await _dispatch_graph_stream(
        request=request,
        response=response,
        graph_name=PLAN_ANALYZE_GRAPH,
        payload=body.graph_payload(),
        session_id=_resolve_identifier(body.session_id, session_id),
        page_id=_resolve_identifier(body.page_id, page_id),
        request_id=_resolve_identifier(body.request_id, request_id),
        graph_stream_dispatch_service=graph_stream_dispatch_service,
    )


@router.post(
    "/graphs/tool-agent/stream",
    response_model=ResultResponse[str],
    operation_id="streamToolAgentGraph",
)
async def stream_tool_agent_graph(
    request: Request,
    response: Response,
    body: ToolAgentGraphRequest,
    session_id: str | None = Query(default=None, alias="sessionId"),
    page_id: str | None = Query(default=None, alias="pageId"),
    request_id: str | None = Query(default=None, alias="requestId"),
    graph_stream_dispatch_service: GraphStreamDispatchService = Depends(
        get_graph_stream_dispatch_service
    ),
) -> ResultResponse[str]:
    return await _dispatch_graph_stream(
        request=request,
        response=response,
        graph_name=TOOL_AGENT_GRAPH,
        payload=body.graph_payload(),
        session_id=_resolve_identifier(body.session_id, session_id),
        page_id=_resolve_identifier(body.page_id, page_id),
        request_id=_resolve_identifier(body.request_id, request_id),
        graph_stream_dispatch_service=graph_stream_dispatch_service,
    )


@router.post(
    "/graphs/image-agent/stream",
    response_model=ResultResponse[str],
    operation_id="streamImageAgentGraph",
)
async def stream_image_agent_graph(
    request: Request,
    response: Response,
    body: ImageAgentGraphRequest,
    session_id: str | None = Query(default=None, alias="sessionId"),
    page_id: str | None = Query(default=None, alias="pageId"),
    request_id: str | None = Query(default=None, alias="requestId"),
    graph_stream_dispatch_service: GraphStreamDispatchService = Depends(
        get_graph_stream_dispatch_service
    ),
) -> ResultResponse[str]:
    return await _dispatch_graph_stream(
        request=request,
        response=response,
        graph_name=IMAGE_AGENT_GRAPH,
        payload=body.graph_payload(),
        session_id=_resolve_identifier(body.session_id, session_id),
        page_id=_resolve_identifier(body.page_id, page_id),
        request_id=_resolve_identifier(body.request_id, request_id),
        graph_stream_dispatch_service=graph_stream_dispatch_service,
    )


@router.post(
    "/graphs/image-analyze-calories/stream",
    response_model=ResultResponse[str],
    operation_id="streamImageAnalyzeCaloriesGraph",
)
async def stream_image_analyze_calories_graph(
    request: Request,
    response: Response,
    body: ImageCaloriesGraphRequest,
    session_id: str | None = Query(default=None, alias="sessionId"),
    page_id: str | None = Query(default=None, alias="pageId"),
    request_id: str | None = Query(default=None, alias="requestId"),
    graph_stream_dispatch_service: GraphStreamDispatchService = Depends(
        get_graph_stream_dispatch_service
    ),
) -> ResultResponse[str]:
    return await _dispatch_graph_stream(
        request=request,
        response=response,
        graph_name=IMAGE_ANALYZE_CALORIES_GRAPH,
        payload=body.graph_payload(),
        session_id=_resolve_identifier(body.session_id, session_id),
        page_id=_resolve_identifier(body.page_id, page_id),
        request_id=_resolve_identifier(body.request_id, request_id),
        graph_stream_dispatch_service=graph_stream_dispatch_service,
    )


@router.post(
    "/graphs/{graph_name}/invoke",
    response_model=GraphInvokeResult,
    include_in_schema=False,
)
async def invoke_graph(
    request: Request,
    response: Response,
    graph_name: str,
    payload: dict[str, Any],
    session_id: str | None = Query(default=None, alias="sessionId"),
    graph_service: GraphService = Depends(get_graph_service),
) -> ResultResponse[dict[str, Any]]:
    normalized_payload, body_session_id, _, _ = _normalize_graph_payload(graph_name, payload)
    return await _invoke_graph(
        request=request,
        response=response,
        graph_name=graph_name,
        payload=normalized_payload,
        session_id=_resolve_identifier(body_session_id, session_id),
        graph_service=graph_service,
    )


@router.post(
    "/graphs/{graph_name}/stream",
    response_model=ResultResponse[str],
    include_in_schema=False,
)
async def stream_graph(
    request: Request,
    response: Response,
    graph_name: str,
    payload: dict[str, Any],
    session_id: str | None = Query(default=None, alias="sessionId"),
    page_id: str | None = Query(default=None, alias="pageId"),
    request_id: str | None = Query(default=None, alias="requestId"),
    graph_stream_dispatch_service: GraphStreamDispatchService = Depends(
        get_graph_stream_dispatch_service
    ),
) -> ResultResponse[str]:
    normalized_payload, body_session_id, body_request_id, body_page_id = _normalize_graph_payload(
        graph_name,
        payload,
    )
    return await _dispatch_graph_stream(
        request=request,
        response=response,
        graph_name=graph_name,
        payload=normalized_payload,
        session_id=_resolve_identifier(body_session_id, session_id),
        page_id=_resolve_identifier(body_page_id, page_id),
        request_id=_resolve_identifier(body_request_id, request_id),
        graph_stream_dispatch_service=graph_stream_dispatch_service,
    )


async def _invoke_graph(
    *,
    request: Request,
    response: Response,
    graph_name: str,
    payload: dict[str, Any],
    session_id: str | None,
    graph_service: GraphService,
) -> ResultResponse[dict[str, Any]]:
    request_context = build_graph_request_context(request)
    trace_headers = build_trace_response_headers(request_context.trace_id)
    _logger.info("Graph invoke  graph=%s  session=%s", graph_name, session_id or "-")
    try:
        result = await graph_service.invoke(
            graph_name=graph_name,
            payload=payload,
            session_id=_resolve_optional_id(session_id),
            request_context=request_context,
        )
    except GraphNotFoundError as exc:
        _logger.warning("Graph invoke rejected – graph not found  graph=%s  error=%s", graph_name, exc)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
            headers=trace_headers,
        ) from exc
    except GraphPayloadValidationError as exc:
        _logger.warning(
            "Graph invoke rejected – payload invalid  graph=%s  errors=%s",
            graph_name,
            exc.errors,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=exc.errors,
            headers=trace_headers,
        ) from exc
    except (ChatModelBuildError, MCPConfigurationError, MCPToolResolutionError) as exc:
        _logger.error(
            "Graph invoke failed  graph=%s  error=%s",
            graph_name,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
            headers=trace_headers,
        ) from exc

    _logger.info(
        "Graph invoke completed  graph=%s  session=%s",
        result.graph_name,
        result.session_id,
    )
    response.headers.update(trace_headers)
    return ResultResponse(data=result.output.model_dump())


async def _dispatch_graph_stream(
    *,
    request: Request,
    response: Response,
    graph_name: str,
    payload: dict[str, Any],
    session_id: str | None,
    page_id: str | None,
    request_id: str | None,
    graph_stream_dispatch_service: GraphStreamDispatchService,
) -> ResultResponse[str]:
    request_context = build_graph_request_context(request)
    trace_headers = build_trace_response_headers(request_context.trace_id)
    resolved_session_id = _require_non_empty_id(
        session_id,
        field_name="sessionId",
        headers=trace_headers,
    )
    resolved_page_id = _resolve_optional_id(page_id)
    resolved_request_id = _resolve_optional_id(request_id)

    _logger.info(
        "Graph stream dispatch  graph=%s  session=%s  page=%s  requestId=%s",
        graph_name,
        resolved_session_id,
        resolved_page_id or "-",
        resolved_request_id or "-",
    )
    try:
        accepted = await graph_stream_dispatch_service.execute(
            graph_name=graph_name,
            payload=graph_stream_payload_from_input(payload),
            session_id=resolved_session_id,
            page_id=resolved_page_id,
            request_id=resolved_request_id,
            request_context=request_context,
        )
    except SseConnectionNotFoundError as exc:
        _logger.warning(
            "Graph stream dispatch rejected – SSE connection not found  graph=%s  session=%s  error=%s",
            graph_name,
            resolved_session_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
            headers=trace_headers,
        ) from exc

    _logger.info(
        "Graph stream accepted  graph=%s  session=%s  requestId=%s",
        accepted.graph_name,
        accepted.session_id,
        accepted.request_id,
    )
    response.headers.update(trace_headers)
    return ResultResponse(data=accepted.request_id)


def _require_non_empty_id(
    value: str | None,
    *,
    field_name: str,
    headers: dict[str, str],
) -> str:
    candidate = _resolve_optional_id(value)
    if candidate is not None:
        return candidate
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=f"{field_name} must not be blank",
        headers=headers,
    )


def _resolve_identifier(primary: str | None, fallback: str | None) -> str | None:
    candidate = _resolve_optional_id(primary)
    if candidate is not None:
        return candidate
    return _resolve_optional_id(fallback)


def _resolve_optional_id(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    return candidate or None


def _normalize_graph_payload(
    graph_name: str,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], str | None, str | None, str | None]:
    normalized = {str(key): value for key, value in dict(payload).items()}
    session_id = _pop_optional_alias(normalized, "sessionId", "session_id")
    request_id = _pop_optional_alias(normalized, "requestId", "request_id")
    page_id = _pop_optional_alias(normalized, "pageId", "page_id")

    if graph_name == TEXT_ANALYSIS_GRAPH:
        _apply_first_alias(normalized, target="text", aliases=("message",))
    elif graph_name in {PLAN_ANALYZE_GRAPH, TOOL_AGENT_GRAPH}:
        _apply_first_alias(normalized, target="query", aliases=("message",))
    elif graph_name in {IMAGE_AGENT_GRAPH, IMAGE_ANALYZE_CALORIES_GRAPH}:
        _apply_first_alias(normalized, target="image_url", aliases=("imageUrl",))
        _apply_first_alias(
            normalized,
            target="text",
            aliases=("message", "description"),
        )

    normalized.pop("message", None)
    normalized.pop("description", None)
    normalized.pop("imageUrl", None)
    return normalized, session_id, request_id, page_id


def _apply_first_alias(
    payload: dict[str, Any],
    *,
    target: str,
    aliases: tuple[str, ...],
) -> None:
    current_value = payload.get(target)
    if current_value not in (None, ""):
        return
    for alias in aliases:
        if alias not in payload:
            continue
        alias_value = payload[alias]
        if alias_value in (None, ""):
            continue
        payload[target] = alias_value
        return


def _pop_optional_alias(payload: dict[str, Any], *aliases: str) -> str | None:
    for alias in aliases:
        value = payload.pop(alias, None)
        if isinstance(value, str):
            candidate = value.strip()
            if candidate:
                return candidate
    return None
