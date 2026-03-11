from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from overmindagent.api.dependencies import get_graph_service
from overmindagent.graphs.registry import GraphNotFoundError
from overmindagent.llm.factory import MissingLLMConfigurationError
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
        result = graph_service.invoke(graph_name=graph_name, payload=payload)
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
