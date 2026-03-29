from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from .stream_events import StreamEvent

_logger = logging.getLogger(__name__)


@runtime_checkable
class StreamEventSink(Protocol):
    async def publish(self, event: StreamEvent) -> None: ...


class InProcessStreamEventBus:
    """
    Lightweight in-process async event bus.

    Producers call publish/publish_many and return immediately after all
    registered sinks have been awaited.  Any sink that raises is isolated
    – its exception is logged and the remaining sinks still receive the event.
    """

    def __init__(self) -> None:
        self._sinks: list[StreamEventSink] = []

    def subscribe(self, sink: StreamEventSink) -> None:
        self._sinks.append(sink)

    async def publish(self, event: StreamEvent) -> None:
        for sink in self._sinks:
            try:
                await sink.publish(event)
            except Exception:
                _logger.exception(
                    "Stream event sink raised an exception; event suppressed for this sink",
                    extra={
                        "event_kind": event.kind,
                        "event_id": event.event_id,
                        "session_id": event.target.session_id,
                    },
                )

    async def publish_many(self, events: Sequence[StreamEvent]) -> None:
        for event in events:
            await self.publish(event)
