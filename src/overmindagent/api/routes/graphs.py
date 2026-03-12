from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from overmindagent.api.dependencies import get_graph_service
from overmindagent.graphs.registry import GraphNotFoundError
from overmindagent.llm import MissingLLMConfigurationError
from overmindagent.schemas.api import GraphInvokeResponse
from overmindagent.schemas.analysis import TextAnalysisRequest
from overmindagent.services.graph_service import GraphService

router = APIRouter(tags=["graphs"])


@router.post("/graphs/{graph_name}/invoke", response_model=GraphInvokeResponse)
async def invoke_graph(
    graph_name: str,
    payload: TextAnalysisRequest,
    graph_service: GraphService = Depends(get_graph_service),
) -> GraphInvokeResponse:
    try:
        result = await graph_service.invoke(graph_name=graph_name, payload=payload)
    except GraphNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except MissingLLMConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    return GraphInvokeResponse(
        graph_name=result.graph_name,
        session_id=result.session_id,
        data=result.output,
    )


@router.post("/graphs/{graph_name}/stream")
async def stream_graph(
    graph_name: str,
    payload: TextAnalysisRequest,
    graph_service: GraphService = Depends(get_graph_service),
) -> StreamingResponse:
    async def event_generator():
        try:
            async for chunk in graph_service.stream(graph_name=graph_name, payload=payload):
                yield chunk
        except GraphNotFoundError as exc:
            yield f"event: error\ndata: {{\"detail\": \"{str(exc)}\"}}\n\n"
        except MissingLLMConfigurationError as exc:
            yield f"event: error\ndata: {{\"detail\": \"{str(exc)}\"}}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
