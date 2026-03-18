from __future__ import annotations

from collections.abc import Callable, Sequence

from langchain_core.tools import BaseTool

from overmindagent.common.auth import AuthenticatedUser
from overmindagent.tools import build_toolset

from .client import MCPClientFactory
from .models import MCPSettings


class MCPToolResolver:
    def __init__(
        self,
        settings: MCPSettings,
        client_factory: MCPClientFactory | None = None,
        local_tools_builder: Callable[[], list[BaseTool]] = build_toolset,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory or MCPClientFactory(settings)
        self._local_tools_builder = local_tools_builder

    async def resolve_tools(
        self,
        *,
        graph_name: str,
        server_names: Sequence[str],
        current_user: AuthenticatedUser,
        request_headers: dict[str, str],
    ) -> list[BaseTool]:
        _ = graph_name
        _ = current_user

        local_tools = list(self._local_tools_builder())
        if not self._settings.enabled:
            return local_tools
        if not server_names:
            return local_tools

        remote_tools = await self._client_factory.get_tools_for_servers(
            server_names=server_names,
            request_headers=request_headers,
        )
        return self._merge_tools(local_tools, remote_tools)

    @staticmethod
    def _merge_tools(
        local_tools: Sequence[BaseTool],
        remote_tools: Sequence[BaseTool],
    ) -> list[BaseTool]:
        merged_tools: list[BaseTool] = []
        seen_tool_names: set[str] = set()

        for tool in [*local_tools, *remote_tools]:
            tool_name = str(tool.name).strip()
            if not tool_name or tool_name in seen_tool_names:
                continue
            seen_tool_names.add(tool_name)
            merged_tools.append(tool)

        return merged_tools

