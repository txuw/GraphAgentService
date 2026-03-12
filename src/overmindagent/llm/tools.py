from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel

from .schemas import ToolCall, ToolResult, ToolSpec

ToolHandler = Callable[[dict[str, Any]], Awaitable[Any]]


class ToolRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, ToolHandler] = {}
        self._specs: dict[str, ToolSpec] = {}

    def register(
        self,
        name: str,
        description: str,
        input_model: type[BaseModel],
        handler: ToolHandler,
    ) -> None:
        self._handlers[name] = handler
        self._specs[name] = ToolSpec(
            name=name,
            description=description,
            input_schema=input_model.model_json_schema(),
        )

    def list_specs(self) -> list[ToolSpec]:
        return list(self._specs.values())

    async def execute(self, tool_call: ToolCall) -> ToolResult:
        handler = self._handlers.get(tool_call.name)
        if handler is None:
            return ToolResult(
                call_id=tool_call.id,
                output=f"Unknown tool: {tool_call.name}",
                is_error=True,
            )

        try:
            output = await handler(tool_call.arguments)
        except Exception as exc:  # pragma: no cover - defensive boundary
            return ToolResult(call_id=tool_call.id, output=str(exc), is_error=True)

        return ToolResult(call_id=tool_call.id, output=self._serialize_output(output))

    @staticmethod
    def _serialize_output(output: Any) -> str:
        if isinstance(output, str):
            return output
        return json.dumps(output, ensure_ascii=True, default=str)
