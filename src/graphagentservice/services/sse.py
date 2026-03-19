from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import count
from uuid import uuid4


class SseConnectionNotFoundError(LookupError):
    pass


@dataclass(slots=True, frozen=True)
class SseEventMessage:
    event: str
    id: str
    retry: int
    data: str

    def encode(self) -> str:
        return (
            f"id: {self.id}\n"
            f"event: {self.event}\n"
            f"retry: {self.retry}\n"
            f"data: {self.data}\n\n"
        )


@dataclass(slots=True)
class SseConnection:
    connection_id: str
    session_id: str
    page_id: str
    last_event_id: str | None = None
    connected_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    queue: asyncio.Queue[SseEventMessage | None] = field(default_factory=asyncio.Queue)
    _sequence: count = field(default_factory=lambda: count(1), repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    def next_sequence(self) -> int:
        return next(self._sequence)

    @property
    def is_closed(self) -> bool:
        return self._closed

    async def push(self, message: SseEventMessage) -> None:
        if self._closed:
            raise SseConnectionNotFoundError(
                f"SSE connection is closed: session_id={self.session_id}, page_id={self.page_id}",
            )
        await self.queue.put(message)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self.queue.put(None)


class SseConnectionRegistry:
    def __init__(
        self,
        *,
        retry_ms: int = 3000,
        heartbeat_interval: float = 15.0,
    ) -> None:
        self._retry_ms = retry_ms
        self._heartbeat_interval = heartbeat_interval
        self._connections: dict[tuple[str, str], SseConnection] = {}

    async def register(
        self,
        *,
        session_id: str,
        page_id: str,
        last_event_id: str | None = None,
    ) -> SseConnection:
        key = (session_id, page_id)
        existing = self._connections.get(key)
        if existing is not None:
            await existing.close()

        connection = SseConnection(
            connection_id=uuid4().hex,
            session_id=session_id,
            page_id=page_id,
            last_event_id=last_event_id,
        )
        self._connections[key] = connection
        return connection

    def get(self, *, session_id: str, page_id: str) -> SseConnection | None:
        return self._connections.get((session_id, page_id))

    def require(self, *, session_id: str, page_id: str) -> SseConnection:
        connection = self.get(session_id=session_id, page_id=page_id)
        if connection is None or connection.is_closed:
            raise SseConnectionNotFoundError(
                f"SSE connection not found: session_id={session_id}, page_id={page_id}",
            )
        return connection

    async def unregister(self, connection: SseConnection) -> None:
        key = (connection.session_id, connection.page_id)
        current = self._connections.get(key)
        if current is connection:
            self._connections.pop(key, None)
        await connection.close()

    async def close_all(self) -> None:
        connections = list(self._connections.values())
        self._connections.clear()
        for connection in connections:
            await connection.close()

    async def send_connected_event(self, connection: SseConnection) -> None:
        await self.send(
            session_id=connection.session_id,
            page_id=connection.page_id,
            event="connected",
            payload={
                "connection_id": connection.connection_id,
                "session_id": connection.session_id,
                "page_id": connection.page_id,
                "last_event_id": connection.last_event_id,
                "connected_at": connection.connected_at.isoformat(),
            },
        )

    async def send(
        self,
        *,
        session_id: str,
        page_id: str,
        event: str,
        payload: dict[str, object],
    ) -> None:
        connection = self.require(session_id=session_id, page_id=page_id)
        await connection.push(self._build_message(connection, event=event, payload=payload))

    async def event_stream(
        self,
        connection: SseConnection,
        *,
        is_disconnected: Callable[[], Awaitable[bool]] | None = None,
    ) -> AsyncIterator[str]:
        try:
            while True:
                try:
                    message = await asyncio.wait_for(
                        connection.queue.get(),
                        timeout=self._heartbeat_interval,
                    )
                except TimeoutError:
                    if is_disconnected is not None and await is_disconnected():
                        break
                    yield self._build_message(
                        connection,
                        event="heartbeat",
                        payload={"ts": datetime.now(UTC).isoformat()},
                    ).encode()
                    continue

                if message is None:
                    break

                yield message.encode()

                if is_disconnected is not None and await is_disconnected():
                    break
        finally:
            await self.unregister(connection)

    def _build_message(
        self,
        connection: SseConnection,
        *,
        event: str,
        payload: dict[str, object],
    ) -> SseEventMessage:
        return SseEventMessage(
            event=event,
            id=f"{connection.connection_id}:{connection.next_sequence()}",
            retry=self._retry_ms,
            data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )
