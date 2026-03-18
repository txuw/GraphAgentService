from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

TransportType = Literal["streamable_http"]


class MCPConnectionSettings(BaseModel):
    enabled: bool = True
    transport: TransportType = "streamable_http"
    url: str = Field(min_length=1)
    headers: dict[str, str] = Field(default_factory=dict)
    server_description: str = ""

    @field_validator("url")
    @classmethod
    def _normalize_url(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("MCP connection url must not be empty.")
        return normalized

    @field_validator("headers", mode="before")
    @classmethod
    def _normalize_headers(cls, value: object) -> dict[str, str]:
        if value is None:
            return {}
        if isinstance(value, dict):
            return {
                str(key): str(header_value)
                for key, header_value in value.items()
                if key is not None and header_value is not None
            }
        items = getattr(value, "items", None)
        if callable(items):
            return {
                str(key): str(header_value)
                for key, header_value in items()
                if key is not None and header_value is not None
            }
        raise TypeError("MCP connection headers must be a mapping.")


class MCPSettings(BaseModel):
    enabled: bool = False
    request_timeout: float = 30.0
    tool_cache_ttl_seconds: int = 300
    connections: dict[str, MCPConnectionSettings] = Field(default_factory=dict)

    @field_validator("request_timeout")
    @classmethod
    def _validate_request_timeout(cls, value: float) -> float:
        normalized = float(value)
        if normalized <= 0:
            raise ValueError("MCP request_timeout must be positive.")
        return normalized

    @field_validator("tool_cache_ttl_seconds")
    @classmethod
    def _validate_tool_cache_ttl_seconds(cls, value: int) -> int:
        normalized = int(value)
        if normalized < 0:
            raise ValueError("MCP tool_cache_ttl_seconds must be non-negative.")
        return normalized

