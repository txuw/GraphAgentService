from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .graph_service import GraphRequestContext
from .graph_stream_service import GraphStreamDispatchService


@dataclass(slots=True)
class ChatStreamAccepted:
    graph_name: str
    session_id: str
    page_id: str | None
    request_id: str


class ChatStreamService:
    def __init__(self, dispatch_service: GraphStreamDispatchService) -> None:
        self._dispatch_service = dispatch_service

    async def execute(
        self,
        *,
        graph_name: str,
        payload: dict[str, Any],
        session_id: str,
        page_id: str,
        request_id: str | None = None,
        request_context: GraphRequestContext | None = None,
    ) -> ChatStreamAccepted:
        accepted = await self._dispatch_service.execute(
            graph_name=graph_name,
            payload=payload,
            session_id=session_id,
            page_id=page_id,
            request_id=request_id,
            request_context=request_context,
        )
        return ChatStreamAccepted(
            graph_name=accepted.graph_name,
            session_id=accepted.session_id,
            page_id=accepted.page_id,
            request_id=accepted.request_id,
        )
