from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from overmindagent.common.auth import AuthenticatedUser, AuthenticationError, LogtoAuthenticator
from overmindagent.services.chat_stream_service import ChatStreamService
from overmindagent.services.graph_service import GraphRequestContext, GraphService
from overmindagent.services.sse import SseConnectionRegistry


def get_graph_service(request: Request) -> GraphService:
    return request.app.state.graph_service


def get_chat_stream_service(request: Request) -> ChatStreamService:
    return request.app.state.chat_stream_service


def get_sse_connection_registry(request: Request) -> SseConnectionRegistry:
    return request.app.state.sse_connection_registry


def get_authenticator(request: Request) -> LogtoAuthenticator:
    return request.app.state.logto_authenticator


def require_current_user(
    request: Request,
    authenticator: LogtoAuthenticator = Depends(get_authenticator),
) -> AuthenticatedUser:
    try:
        return authenticator.authenticate_request(request)
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from exc


def get_current_user(request: Request) -> AuthenticatedUser:
    current_user = getattr(request.state, "current_user", None)
    if isinstance(current_user, AuthenticatedUser):
        return current_user
    return AuthenticatedUser.anonymous()


def build_graph_request_context(request: Request) -> GraphRequestContext:
    return GraphRequestContext(
        current_user=get_current_user(request),
        request_headers=dict(request.headers),
    )
