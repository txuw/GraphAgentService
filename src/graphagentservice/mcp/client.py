from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from time import monotonic
from typing import Any

from langchain_core.tools import BaseTool

from .headers import MCPHeaderForwarder
from .models import MCPSettings


class MCPConfigurationError(ValueError):
    pass


class MCPToolResolutionError(RuntimeError):
    pass


@dataclass(slots=True)
class _ToolCacheEntry:
    expires_at: float
    tools: tuple[BaseTool, ...]


def load_multi_server_mcp_client() -> type[Any]:
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except ImportError as exc:
        raise MCPConfigurationError(
            "langchain-mcp-adapters is not installed."
        ) from exc
    return MultiServerMCPClient


class MCPClientFactory:
    def __init__(
        self,
        settings: MCPSettings,
        header_forwarder: MCPHeaderForwarder | None = None,
    ) -> None:
        self._settings = settings
        self._header_forwarder = header_forwarder or MCPHeaderForwarder()
        self._cache: dict[str, _ToolCacheEntry] = {}
        self._cache_lock = asyncio.Lock()

    async def get_tools_for_servers(
        self,
        *,
        server_names: Sequence[str],
        request_headers: dict[str, str],
    ) -> list[BaseTool]:
        if not self._settings.enabled:
            return []

        resolved_server_names = self._normalize_server_names(server_names)
        if not resolved_server_names:
            return []

        client_config = self._build_client_config(
            server_names=resolved_server_names,
            request_headers=request_headers,
        )
        cache_key = self._build_cache_key(
            server_names=resolved_server_names,
            client_config=client_config,
        )

        cached_tools = await self._get_cached_tools(cache_key)
        if cached_tools is not None:
            return cached_tools

        client_class = load_multi_server_mcp_client()
        client = client_class(client_config)
        try:
            resolved_tools = list(await client.get_tools())
        except Exception as exc:
            raise MCPToolResolutionError(
                "Failed to fetch tools from MCP servers."
            ) from exc

        await self._store_cached_tools(
            cache_key,
            resolved_tools,
        )
        return list(resolved_tools)

    def _build_client_config(
        self,
        *,
        server_names: tuple[str, ...],
        request_headers: dict[str, str],
    ) -> dict[str, dict[str, Any]]:
        client_config: dict[str, dict[str, Any]] = {}
        for server_name in server_names:
            connection = self._settings.connections.get(server_name)
            if connection is None:
                raise MCPConfigurationError(f"Unknown MCP server: {server_name}")
            if not connection.enabled:
                raise MCPConfigurationError(f"MCP server is disabled: {server_name}")
            if connection.transport != "streamable_http":
                raise MCPConfigurationError(
                    f"Unsupported MCP transport for {server_name}: {connection.transport}"
                )

            client_config[server_name] = {
                "transport": self._to_client_transport(connection.transport),
                "url": connection.url,
                "headers": self._header_forwarder.build_forward_headers(
                    request_headers=request_headers,
                    connection_headers=connection.headers,
                ),
                "timeout": self._settings.request_timeout,
            }
        return client_config

    async def _get_cached_tools(self, cache_key: str) -> list[BaseTool] | None:
        async with self._cache_lock:
            cached_entry = self._cache.get(cache_key)
            if cached_entry is None:
                return None
            if cached_entry.expires_at < monotonic():
                self._cache.pop(cache_key, None)
                return None
            return list(cached_entry.tools)

    async def _store_cached_tools(
        self,
        cache_key: str,
        tools: Sequence[BaseTool],
    ) -> None:
        ttl = self._settings.tool_cache_ttl_seconds
        if ttl <= 0:
            return

        async with self._cache_lock:
            self._cache[cache_key] = _ToolCacheEntry(
                expires_at=monotonic() + ttl,
                tools=tuple(tools),
            )

    @staticmethod
    def _normalize_server_names(server_names: Sequence[str]) -> tuple[str, ...]:
        normalized_names: list[str] = []
        seen_names: set[str] = set()
        for server_name in server_names:
            candidate = str(server_name).strip()
            if not candidate or candidate in seen_names:
                continue
            seen_names.add(candidate)
            normalized_names.append(candidate)
        return tuple(normalized_names)

    def _build_cache_key(
        self,
        *,
        server_names: tuple[str, ...],
        client_config: dict[str, dict[str, Any]],
    ) -> str:
        fingerprint_parts = [f"timeout:{self._settings.request_timeout}"]
        for server_name in server_names:
            server_config = client_config[server_name]
            fingerprint_parts.extend(
                (
                    f"server:{server_name}",
                    f"url:{server_config['url']}",
                    f"transport:{server_config['transport']}",
                    f"headers:{self._hash_headers(server_config.get('headers', {}))}",
                )
            )
        payload = "|".join(fingerprint_parts)
        return sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _hash_headers(headers: dict[str, str]) -> str:
        parts = [
            f"{str(key).lower()}={str(value)}"
            for key, value in sorted(headers.items(), key=lambda item: item[0].lower())
        ]
        return sha256("|".join(parts).encode("utf-8")).hexdigest()

    @staticmethod
    def _to_client_transport(transport: str) -> str:
        if transport == "streamable_http":
            return "http"
        raise MCPConfigurationError(f"Unsupported MCP transport: {transport}")

