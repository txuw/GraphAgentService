from __future__ import annotations

from fastapi import Request

from overmindagent.services.graph_service import GraphService


def get_graph_service(request: Request) -> GraphService:
    return request.app.state.graph_service
