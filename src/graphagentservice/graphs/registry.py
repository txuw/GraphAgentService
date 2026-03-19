from __future__ import annotations

from dataclasses import replace
from typing import Any

from graphagentservice.common.checkpoint import CheckpointProvider
from graphagentservice.common.config import Settings

from .runtime import GraphRuntime
from .plan_analyze import PlanAnalyzeGraphBuilder
from .text_analysis import TextAnalysisGraphBuilder
from .tool_agent import ToolAgentGraphBuilder
from .image_agent import ImageGraphBuilder
from .image_analyze_calories import ImageAnalyzeCaloriesGraphBuilder


class GraphNotFoundError(KeyError):
    pass


class GraphRegistry:
    def __init__(self, graphs: dict[str, GraphRuntime]) -> None:
        self._graphs = graphs

    def get(self, graph_name: str) -> GraphRuntime:
        try:
            return self._graphs[graph_name]
        except KeyError as exc:
            raise GraphNotFoundError(f"Unknown graph: {graph_name}") from exc

    def list_names(self) -> tuple[str, ...]:
        return tuple(self._graphs.keys())

    def list_runtimes(self) -> tuple[GraphRuntime, ...]:
        return tuple(self._graphs.values())


def create_graph_registry(
    settings: Settings,
    checkpoint_provider: CheckpointProvider,
) -> GraphRegistry:
    checkpointer = checkpoint_provider.build()
    graph_overrides = _graph_overrides(settings)
    runtimes = (
        TextAnalysisGraphBuilder(
            graph_settings=graph_overrides.get(TextAnalysisGraphBuilder.name, {}),
            checkpointer=checkpointer,
        ).build(),
        PlanAnalyzeGraphBuilder(
            graph_settings=graph_overrides.get(PlanAnalyzeGraphBuilder.name, {}),
            checkpointer=checkpointer,
        ).build(),
        ToolAgentGraphBuilder(
            graph_settings=graph_overrides.get(ToolAgentGraphBuilder.name, {}),
            checkpointer=checkpointer,
        ).build(),
        ImageGraphBuilder(
            graph_settings=graph_overrides.get(ImageGraphBuilder.name, {}),
            checkpointer=checkpointer,
        ).build(),
        ImageAnalyzeCaloriesGraphBuilder(
            graph_settings=graph_overrides.get(ImageAnalyzeCaloriesGraphBuilder.name, {}),
            checkpointer=checkpointer,
        ).build(),
    )

    applied_runtimes = tuple(
        _apply_runtime_overrides(
            runtime=runtime,
            graph_settings=graph_overrides.get(runtime.name, {}),
        )
        for runtime in runtimes
    )
    return GraphRegistry({runtime.name: runtime for runtime in applied_runtimes})


def _graph_overrides(settings: Settings) -> dict[str, Any]:
    configured_graphs = settings.get("graphs", {})
    if hasattr(configured_graphs, "items"):
        return {
            _normalize_graph_name(graph_name): graph_settings
            for graph_name, graph_settings in configured_graphs.items()
        }
    return {}


def _normalize_graph_name(name: str) -> str:
    return str(name).replace("_", "-")


def _apply_runtime_overrides(
    *,
    runtime: GraphRuntime,
    graph_settings: Any,
) -> GraphRuntime:
    return replace(
        runtime,
        mcp_servers=_read_mcp_servers(graph_settings),
    )


def _read_mcp_servers(graph_settings: Any) -> tuple[str, ...]:
    if hasattr(graph_settings, "get"):
        raw_value = graph_settings.get("mcp_servers", ())
    else:
        raw_value = ()

    if isinstance(raw_value, str):
        candidate = raw_value.strip()
        return (candidate,) if candidate else ()

    if not isinstance(raw_value, (list, tuple)):
        return ()

    normalized_servers: list[str] = []
    for server_name in raw_value:
        candidate = str(server_name).strip()
        if candidate:
            normalized_servers.append(candidate)
    return tuple(normalized_servers)
