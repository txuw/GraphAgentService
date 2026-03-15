from __future__ import annotations

from fastapi import Request

from overmindagent.services.chat_stream_service import ChatStreamService
from overmindagent.services.graph_service import GraphService
from overmindagent.services.sse import SseConnectionRegistry


def get_graph_service(request: Request) -> GraphService:
    return request.app.state.graph_service


def get_chat_stream_service(request: Request) -> ChatStreamService:
    return request.app.state.chat_stream_service


def get_sse_connection_registry(request: Request) -> SseConnectionRegistry:
    return request.app.state.sse_connection_registry
