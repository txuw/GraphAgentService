from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langgraph.prebuilt import ToolNode

if TYPE_CHECKING:
    from .stream_event_bus import StreamEventSink
    from .stream_events import StreamEventFactory

_logger = logging.getLogger(__name__)


class ToolStreamEventEmitter:
    """
    Emits tool lifecycle stream events (start / done / error) to the bus.

    Holds a factory (which knows the request target and sequence counter) and a
    reference to the bus sink so it can publish without knowing anything about
    SSE or wire formats.
    """

    def __init__(self, *, factory: StreamEventFactory, bus: StreamEventSink) -> None:
        self._factory = factory
        self._bus = bus

    @property
    def factory(self) -> StreamEventFactory:
        return self._factory

    async def emit_start(self, tool_name: str) -> None:
        await self._bus.publish(
            self._factory.build_tool_event(tool_name=tool_name, phase="start")
        )

    async def emit_done(self, tool_name: str) -> None:
        await self._bus.publish(
            self._factory.build_tool_event(tool_name=tool_name, phase="done")
        )

    async def emit_error(self, tool_name: str, error_message: str) -> None:
        await self._bus.publish(
            self._factory.build_tool_event(
                tool_name=tool_name,
                phase="error",
                error_message=error_message,
            )
        )


class ObservedToolNode(ToolNode):
    """
    A ToolNode that emits tool lifecycle stream events around each tool call.

    Individual tool objects are never wrapped or replaced – we observe at the
    ToolNode level so that LangChain's internal response_format / artifact
    protocol is never disturbed.

    Emits:
    - tool_start  for every tool call before execution begins
    - tool_done   for every tool call that succeeds
    - tool_error  for every tool call that fails (either raised or status="error")
    """

    def __init__(
        self,
        tools: Sequence[BaseTool],
        *,
        emitter: ToolStreamEventEmitter,
    ) -> None:
        super().__init__(list(tools))
        self._emitter = emitter

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        messages = input.get("messages", []) if isinstance(input, dict) else []
        last = messages[-1] if messages else None
        tool_calls: list[dict[str, Any]] = list(getattr(last, "tool_calls", []))

        tool_names = [str(c.get("name", "unknown")) for c in tool_calls]
        if tool_names:
            _logger.info("Tool calls starting  tools=[%s]  count=%d", ", ".join(tool_names), len(tool_names))

        for call in tool_calls:
            await self._emitter.emit_start(str(call.get("name", "unknown")))

        t0 = time.perf_counter()
        try:
            result = await super().ainvoke(input, config, **kwargs)
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            _logger.error(
                "Tool invocation exception  tools=[%s]  error=%s  elapsed=%.0fms",
                ", ".join(tool_names),
                exc,
                elapsed_ms,
                exc_info=True,
            )
            for call in tool_calls:
                await self._emitter.emit_error(str(call.get("name", "unknown")), str(exc))
            raise

        elapsed_ms = (time.perf_counter() - t0) * 1000
        output_messages = result.get("messages", []) if isinstance(result, dict) else []
        error_call_ids: set[str] = {
            str(msg.tool_call_id)
            for msg in output_messages
            if isinstance(msg, ToolMessage) and msg.status == "error"
        }
        id_to_error_text: dict[str, str] = {
            str(msg.tool_call_id): _message_text(msg)
            for msg in output_messages
            if isinstance(msg, ToolMessage) and msg.status == "error"
        }

        failed_names: list[str] = []
        for call in tool_calls:
            call_id = str(call.get("id", ""))
            name = str(call.get("name", "unknown"))
            if call_id in error_call_ids:
                failed_names.append(name)
                await self._emitter.emit_error(name, id_to_error_text.get(call_id, "tool error"))
            else:
                await self._emitter.emit_done(name)

        if failed_names:
            _logger.warning(
                "Tool calls completed with errors  tools=[%s]  failed=[%s]  elapsed=%.0fms",
                ", ".join(tool_names),
                ", ".join(failed_names),
                elapsed_ms,
            )
        elif tool_names:
            _logger.info(
                "Tool calls completed  tools=[%s]  elapsed=%.0fms",
                ", ".join(tool_names),
                elapsed_ms,
            )

        return result


def _message_text(msg: ToolMessage) -> str:
    if isinstance(msg.content, str):
        return msg.content
    if isinstance(msg.content, list):
        parts: list[str] = []
        for block in msg.content:
            if isinstance(block, dict) and "text" in block:
                parts.append(str(block["text"]))
            else:
                parts.append(str(block))
        return "".join(parts)
    return str(msg.content)
