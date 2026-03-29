from __future__ import annotations

import logging

from graphagentservice.schemas.api import AgentStreamEvent
from graphagentservice.services.sse import SseConnectionNotFoundError, SseConnectionRegistry

from .stream_events import StreamEvent, StreamEventKind

_logger = logging.getLogger(__name__)

_TERMINAL_KINDS = frozenset({StreamEventKind.AI_DONE, StreamEventKind.AI_ERROR})


class SseStreamEventSink:
    """
    Projects internal StreamEvent objects into AgentStreamEvent wire DTOs and
    delivers them to matching SSE connections via SseConnectionRegistry.

    This is the only place that bridges the internal event domain and the SSE
    wire protocol.  Connection management (register/unregister/heartbeat) stays
    in SseConnectionRegistry and is not affected by this sink.
    """

    def __init__(self, *, registry: SseConnectionRegistry) -> None:
        self._registry = registry

    async def publish(self, event: StreamEvent) -> None:
        wire = _project(event)
        try:
            await self._registry.publish_agent_event(
                session_id=event.target.session_id,
                user_id=event.target.user_id,
                page_id=event.target.page_id,
                event=wire,
            )
        except SseConnectionNotFoundError:
            _logger.debug(
                "SSE connection not found – event dropped  session=%s  page=%s  kind=%s  requestId=%s",
                event.target.session_id,
                event.target.page_id or "-",
                event.kind.value,
                event.target.request_id,
            )


def _project(event: StreamEvent) -> AgentStreamEvent:
    return AgentStreamEvent(
        session_id=event.target.session_id,
        request_id=event.target.request_id,
        trace_id=event.target.trace_id or None,
        event_type=event.kind.value,
        event_id=event.event_id,
        seq=event.seq,
        content=event.content,
        done=event.kind in _TERMINAL_KINDS,
        finish_reason=event.finish_reason,
        code=event.code,
        message=event.message,
        retriable=event.retriable,
    )
