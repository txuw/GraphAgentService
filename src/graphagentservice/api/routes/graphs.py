from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel

from graphagentservice.api.dependencies import (
    build_graph_request_context,
    get_graph_service,
    get_graph_stream_dispatch_service,
)
from graphagentservice.common.trace import build_trace_response_headers
from graphagentservice.graphs.registry import GraphNotFoundError
from graphagentservice.llm import ChatModelBuildError
from graphagentservice.mcp import MCPConfigurationError, MCPToolResolutionError
from graphagentservice.schemas.analysis import TextAnalysisOutput, TextAnalysisRequest
from graphagentservice.schemas.api import (
    GraphDescriptorResponse,
    GraphInvokeResponse,
    GraphStreamAcceptedResponse,
    TypedGraphInvokeResponse,
)
from graphagentservice.schemas.image import ImageAgentOutput, ImageAgentRequest
from graphagentservice.schemas.image_calories import (
    ImageCaloriesOutput,
    ImageCaloriesRequest,
)
from graphagentservice.schemas.plan_analyze import PlanAnalyzeOutput, PlanAnalyzeRequest
from graphagentservice.schemas.tool_agent import ToolAgentOutput, ToolAgentRequest
from graphagentservice.services.graph_service import GraphPayloadValidationError, GraphService
from graphagentservice.services.graph_stream_service import (
    GraphStreamDispatchService,
    graph_stream_payload_from_input,
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
    response_model=TypedGraphInvokeResponse[TextAnalysisOutput],
    operation_id="invokeTextAnalysisGraph",
)
async def invoke_text_analysis_graph(
    request: Request,
    response: Response,
    body: TextAnalysisRequest,
    session_id: str | None = Query(default=None, alias="sessionId"),
    graph_service: GraphService = Depends(get_graph_service),
) -> GraphInvokeResponse:
    return await _invoke_graph(
        request=request,
        response=response,
        graph_name=TEXT_ANALYSIS_GRAPH,
        payload=body,
        session_id=session_id,
        graph_service=graph_service,
    )


@router.post(
    "/graphs/plan-analyze/invoke",
    response_model=TypedGraphInvokeResponse[PlanAnalyzeOutput],
    operation_id="invokePlanAnalyzeGraph",
)
async def invoke_plan_analyze_graph(
    request: Request,
    response: Response,
    body: PlanAnalyzeRequest,
    session_id: str | None = Query(default=None, alias="sessionId"),
    graph_service: GraphService = Depends(get_graph_service),
) -> GraphInvokeResponse:
    return await _invoke_graph(
        request=request,
        response=response,
        graph_name=PLAN_ANALYZE_GRAPH,
        payload=body,
        session_id=session_id,
        graph_service=graph_service,
    )


@router.post(
    "/graphs/tool-agent/invoke",
    response_model=TypedGraphInvokeResponse[ToolAgentOutput],
    operation_id="invokeToolAgentGraph",
)
async def invoke_tool_agent_graph(
    request: Request,
    response: Response,
    body: ToolAgentRequest,
    session_id: str | None = Query(default=None, alias="sessionId"),
    graph_service: GraphService = Depends(get_graph_service),
) -> GraphInvokeResponse:
    return await _invoke_graph(
        request=request,
        response=response,
        graph_name=TOOL_AGENT_GRAPH,
        payload=body,
        session_id=session_id,
        graph_service=graph_service,
    )


@router.post(
    "/graphs/image-agent/invoke",
    response_model=TypedGraphInvokeResponse[ImageAgentOutput],
    operation_id="invokeImageAgentGraph",
)
async def invoke_image_agent_graph(
    request: Request,
    response: Response,
    body: ImageAgentRequest,
    session_id: str | None = Query(default=None, alias="sessionId"),
    graph_service: GraphService = Depends(get_graph_service),
) -> GraphInvokeResponse:
    return await _invoke_graph(
        request=request,
        response=response,
        graph_name=IMAGE_AGENT_GRAPH,
        payload=body,
        session_id=session_id,
        graph_service=graph_service,
    )


@router.post(
    "/graphs/image-analyze-calories/invoke",
    response_model=TypedGraphInvokeResponse[ImageCaloriesOutput],
    operation_id="invokeImageAnalyzeCaloriesGraph",
)
async def invoke_image_analyze_calories_graph(
    request: Request,
    response: Response,
    body: ImageCaloriesRequest,
    session_id: str | None = Query(default=None, alias="sessionId"),
    graph_service: GraphService = Depends(get_graph_service),
) -> GraphInvokeResponse:
    return await _invoke_graph(
        request=request,
        response=response,
        graph_name=IMAGE_ANALYZE_CALORIES_GRAPH,
        payload=body,
        session_id=session_id,
        graph_service=graph_service,
    )


@router.post(
    "/graphs/text-analysis/stream",
    response_model=GraphStreamAcceptedResponse,
    operation_id="streamTextAnalysisGraph",
)
async def stream_text_analysis_graph(
    request: Request,
    response: Response,
    body: TextAnalysisRequest,
    session_id: str = Query(..., alias="sessionId"),
    page_id: str = Query(..., alias="pageId"),
    request_id: str | None = Query(default=None, alias="requestId"),
    graph_stream_dispatch_service: GraphStreamDispatchService = Depends(
        get_graph_stream_dispatch_service
    ),
) -> GraphStreamAcceptedResponse:
    return await _dispatch_graph_stream(
        request=request,
        response=response,
        graph_name=TEXT_ANALYSIS_GRAPH,
        payload=body,
        session_id=session_id,
        page_id=page_id,
        request_id=request_id,
        graph_stream_dispatch_service=graph_stream_dispatch_service,
    )


@router.post(
    "/graphs/plan-analyze/stream",
    response_model=GraphStreamAcceptedResponse,
    operation_id="streamPlanAnalyzeGraph",
)
async def stream_plan_analyze_graph(
    request: Request,
    response: Response,
    body: PlanAnalyzeRequest,
    session_id: str = Query(..., alias="sessionId"),
    page_id: str = Query(..., alias="pageId"),
    request_id: str | None = Query(default=None, alias="requestId"),
    graph_stream_dispatch_service: GraphStreamDispatchService = Depends(
        get_graph_stream_dispatch_service
    ),
) -> GraphStreamAcceptedResponse:
    return await _dispatch_graph_stream(
        request=request,
        response=response,
        graph_name=PLAN_ANALYZE_GRAPH,
        payload=body,
        session_id=session_id,
        page_id=page_id,
        request_id=request_id,
        graph_stream_dispatch_service=graph_stream_dispatch_service,
    )


@router.post(
    "/graphs/tool-agent/stream",
    response_model=GraphStreamAcceptedResponse,
    operation_id="streamToolAgentGraph",
)
async def stream_tool_agent_graph(
    request: Request,
    response: Response,
    body: ToolAgentRequest,
    session_id: str = Query(..., alias="sessionId"),
    page_id: str = Query(..., alias="pageId"),
    request_id: str | None = Query(default=None, alias="requestId"),
    graph_stream_dispatch_service: GraphStreamDispatchService = Depends(
        get_graph_stream_dispatch_service
    ),
) -> GraphStreamAcceptedResponse:
    return await _dispatch_graph_stream(
        request=request,
        response=response,
        graph_name=TOOL_AGENT_GRAPH,
        payload=body,
        session_id=session_id,
        page_id=page_id,
        request_id=request_id,
        graph_stream_dispatch_service=graph_stream_dispatch_service,
    )


@router.post(
    "/graphs/image-agent/stream",
    response_model=GraphStreamAcceptedResponse,
    operation_id="streamImageAgentGraph",
)
async def stream_image_agent_graph(
    request: Request,
    response: Response,
    body: ImageAgentRequest,
    session_id: str = Query(..., alias="sessionId"),
    page_id: str = Query(..., alias="pageId"),
    request_id: str | None = Query(default=None, alias="requestId"),
    graph_stream_dispatch_service: GraphStreamDispatchService = Depends(
        get_graph_stream_dispatch_service
    ),
) -> GraphStreamAcceptedResponse:
    return await _dispatch_graph_stream(
        request=request,
        response=response,
        graph_name=IMAGE_AGENT_GRAPH,
        payload=body,
        session_id=session_id,
        page_id=page_id,
        request_id=request_id,
        graph_stream_dispatch_service=graph_stream_dispatch_service,
    )


@router.post(
    "/graphs/image-analyze-calories/stream",
    response_model=GraphStreamAcceptedResponse,
    operation_id="streamImageAnalyzeCaloriesGraph",
)
async def stream_image_analyze_calories_graph(
    request: Request,
    response: Response,
    body: ImageCaloriesRequest,
    session_id: str = Query(..., alias="sessionId"),
    page_id: str = Query(..., alias="pageId"),
    request_id: str | None = Query(default=None, alias="requestId"),
    graph_stream_dispatch_service: GraphStreamDispatchService = Depends(
        get_graph_stream_dispatch_service
    ),
) -> GraphStreamAcceptedResponse:
    return await _dispatch_graph_stream(
        request=request,
        response=response,
        graph_name=IMAGE_ANALYZE_CALORIES_GRAPH,
        payload=body,
        session_id=session_id,
        page_id=page_id,
        request_id=request_id,
        graph_stream_dispatch_service=graph_stream_dispatch_service,
    )


@router.post(
    "/graphs/{graph_name}/invoke",
    response_model=GraphInvokeResponse,
    include_in_schema=False,
)
async def invoke_graph(
    request: Request,
    response: Response,
    graph_name: str,
    payload: dict[str, Any],
    session_id: str | None = Query(default=None, alias="sessionId"),
    graph_service: GraphService = Depends(get_graph_service),
) -> GraphInvokeResponse:
    return await _invoke_graph(
        request=request,
        response=response,
        graph_name=graph_name,
        payload=payload,
        session_id=session_id,
        graph_service=graph_service,
    )


@router.post(
    "/graphs/{graph_name}/stream",
    response_model=GraphStreamAcceptedResponse,
    include_in_schema=False,
)
async def stream_graph(
    request: Request,
    response: Response,
    graph_name: str,
    payload: dict[str, Any],
    session_id: str = Query(..., alias="sessionId"),
    page_id: str = Query(..., alias="pageId"),
    request_id: str | None = Query(default=None, alias="requestId"),
    graph_stream_dispatch_service: GraphStreamDispatchService = Depends(
        get_graph_stream_dispatch_service
    ),
) -> GraphStreamAcceptedResponse:
    return await _dispatch_graph_stream(
        request=request,
        response=response,
        graph_name=graph_name,
        payload=payload,
        session_id=session_id,
        page_id=page_id,
        request_id=request_id,
        graph_stream_dispatch_service=graph_stream_dispatch_service,
    )


async def _invoke_graph(
    *,
    request: Request,
    response: Response,
    graph_name: str,
    payload: BaseModel | dict[str, Any],
    session_id: str | None,
    graph_service: GraphService,
) -> GraphInvokeResponse:
    request_context = build_graph_request_context(request)
    trace_headers = build_trace_response_headers(request_context.trace_id)
    try:
        result = await graph_service.invoke(
            graph_name=graph_name,
            payload=payload,
            session_id=session_id,
            request_context=request_context,
        )
    except GraphNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
            headers=trace_headers,
        ) from exc
    except GraphPayloadValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=exc.errors,
            headers=trace_headers,
        ) from exc
    except (ChatModelBuildError, MCPConfigurationError, MCPToolResolutionError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
            headers=trace_headers,
        ) from exc

    response.headers.update(trace_headers)
    return GraphInvokeResponse(
        graph_name=result.graph_name,
        session_id=result.session_id,
        data=result.output.model_dump(),
    )


async def _dispatch_graph_stream(
    *,
    request: Request,
    response: Response,
    graph_name: str,
    payload: BaseModel | dict[str, Any],
    session_id: str,
    page_id: str,
    request_id: str | None,
    graph_stream_dispatch_service: GraphStreamDispatchService,
) -> GraphStreamAcceptedResponse:
    request_context = build_graph_request_context(request)
    trace_headers = build_trace_response_headers(request_context.trace_id)
    resolved_session_id = _require_non_empty_query_id(
        session_id,
        field_name="sessionId",
        headers=trace_headers,
    )
    resolved_page_id = _require_non_empty_query_id(
        page_id,
        field_name="pageId",
        headers=trace_headers,
    )
    resolved_request_id = _resolve_optional_query_id(request_id)

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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
            headers=trace_headers,
        ) from exc

    response.headers.update(trace_headers)
    return GraphStreamAcceptedResponse(
        graph_name=accepted.graph_name,
        session_id=accepted.session_id,
        page_id=accepted.page_id,
        request_id=accepted.request_id,
    )


def _require_non_empty_query_id(
    value: str,
    *,
    field_name: str,
    headers: dict[str, str],
) -> str:
    candidate = value.strip()
    if candidate:
        return candidate
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=f"{field_name} must not be blank",
        headers=headers,
    )


def _resolve_optional_query_id(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    return candidate or None
