from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from overmindagent.api.dependencies import get_graph_service
from overmindagent.graphs.registry import GraphNotFoundError
from overmindagent.llm import ChatModelBuildError
from overmindagent.schemas.api import GraphDescriptorResponse, GraphInvokeResponse
from overmindagent.services.graph_service import GraphPayloadValidationError, GraphService

router = APIRouter(tags=["graphs"])


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


@router.post("/graphs/{graph_name}/invoke", response_model=GraphInvokeResponse)
async def invoke_graph(
    graph_name: str,
    payload: dict[str, Any],
    graph_service: GraphService = Depends(get_graph_service),
) -> GraphInvokeResponse:
    try:
        result = await graph_service.invoke(graph_name=graph_name, payload=payload)
    except GraphNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except GraphPayloadValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.errors) from exc
    except ChatModelBuildError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    return GraphInvokeResponse(
        graph_name=result.graph_name,
        session_id=result.session_id,
        data=result.output.model_dump(),
    )


@router.post("/graphs/{graph_name}/stream")
async def stream_graph(
    graph_name: str,
    payload: dict[str, Any],
    graph_service: GraphService = Depends(get_graph_service),
) -> StreamingResponse:
    async def event_generator():
        try:
            async for chunk in graph_service.stream(graph_name=graph_name, payload=payload):
                yield chunk
        except GraphNotFoundError as exc:
            yield _to_sse("error", {"detail": str(exc)})
        except GraphPayloadValidationError as exc:
            yield _to_sse("error", {"detail": exc.errors})
        except ChatModelBuildError as exc:
            yield _to_sse("error", {"detail": str(exc)})

    return StreamingResponse(event_generator(), media_type="text/event-stream")


def _to_sse(event: str, data: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=True)}\n\n"
