from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import count
from uuid import uuid4

from graphagentservice.schemas.api import AgentStreamEvent


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
    user_id: str | None = None
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
            raise SseConnectionNotFoundError(self.describe_missing())
        await self.queue.put(message)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self.queue.put(None)

    def matches(
        self,
        *,
        session_id: str,
        user_id: str | None,
        page_id: str | None = None,
    ) -> bool:
        if self.session_id != session_id:
            return False
        if page_id is not None and self.page_id != page_id:
            return False
        return self.user_id == _normalize_user_id(user_id)

    def describe_missing(self) -> str:
        if self.user_id:
            return (
                "SSE connection is closed: "
                f"user_id={self.user_id}, session_id={self.session_id}, page_id={self.page_id}"
            )
        return (
            "SSE connection is closed: "
            f"session_id={self.session_id}, page_id={self.page_id}"
        )


class SseConnectionRegistry:
    def __init__(
        self,
        *,
        retry_ms: int = 3000,
        heartbeat_interval: float = 15.0,
    ) -> None:
        self._retry_ms = retry_ms
        self._heartbeat_interval = heartbeat_interval
        self._connections_by_key: dict[tuple[str | None, str, str], SseConnection] = {}
        self._connections_by_id: dict[str, SseConnection] = {}

    async def register(
        self,
        *,
        session_id: str,
        page_id: str,
        user_id: str | None = None,
        last_event_id: str | None = None,
    ) -> SseConnection:
        normalized_user_id = _normalize_user_id(user_id)
        key = (normalized_user_id, session_id, page_id)
        existing = self._connections_by_key.get(key)
        if existing is not None:
            await self.unregister(existing)

        connection = SseConnection(
            connection_id=uuid4().hex,
            session_id=session_id,
            page_id=page_id,
            user_id=normalized_user_id,
            last_event_id=last_event_id,
        )
        self._connections_by_key[key] = connection
        self._connections_by_id[connection.connection_id] = connection
        return connection

    def get_by_connection_id(self, connection_id: str) -> SseConnection | None:
        return self._connections_by_id.get(connection_id)

    def match(
        self,
        *,
        session_id: str,
        user_id: str | None,
        page_id: str | None = None,
    ) -> list[SseConnection]:
        normalized_user_id = _normalize_user_id(user_id)
        return [
            connection
            for connection in self._connections_by_key.values()
            if not connection.is_closed
            and connection.matches(
                session_id=session_id,
                user_id=normalized_user_id,
                page_id=page_id,
            )
        ]

    def require_connections(
        self,
        *,
        session_id: str,
        user_id: str | None,
        page_id: str | None = None,
    ) -> list[SseConnection]:
        matches = self.match(
            session_id=session_id,
            user_id=user_id,
            page_id=page_id,
        )
        if matches:
            return matches
        raise SseConnectionNotFoundError(
            _missing_message(
                session_id=session_id,
                user_id=user_id,
                page_id=page_id,
            )
        )

    async def unregister(self, connection: SseConnection) -> None:
        key = (connection.user_id, connection.session_id, connection.page_id)
        current = self._connections_by_key.get(key)
        if current is connection:
            self._connections_by_key.pop(key, None)
        self._connections_by_id.pop(connection.connection_id, None)
        await connection.close()

    async def close_all(self) -> None:
        connections = list(self._connections_by_key.values())
        self._connections_by_key.clear()
        self._connections_by_id.clear()
        for connection in connections:
            await connection.close()

    async def send_connected_event(self, connection: SseConnection) -> None:
        seq = connection.next_sequence()
        event = AgentStreamEvent(
            session_id=connection.session_id,
            event_type="connected",
            event_id=f"connected:{connection.connection_id}:{seq}",
            content=json.dumps(
                {
                    "connectionId": connection.connection_id,
                    "userId": connection.user_id,
                    "sessionId": connection.session_id,
                    "pageId": connection.page_id,
                    "serverTime": connection.connected_at.isoformat(),
                    "lastEventId": connection.last_event_id,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            done=False,
        )
        await connection.push(self._build_message(event))

    async def publish_agent_event(
        self,
        *,
        session_id: str,
        user_id: str | None,
        page_id: str | None,
        event: AgentStreamEvent,
    ) -> None:
        connections = self.require_connections(
            session_id=session_id,
            user_id=user_id,
            page_id=page_id,
        )
        message = self._build_message(event)
        for connection in connections:
            try:
                await connection.push(message)
            except SseConnectionNotFoundError:
                await self.unregister(connection)

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
                    seq = connection.next_sequence()
                    yield self._build_message(
                        AgentStreamEvent(
                            session_id=connection.session_id,
                            event_type="heartbeat",
                            event_id=f"heartbeat:{connection.connection_id}:{seq}",
                            content=json.dumps(
                                {"ts": datetime.now(UTC).isoformat()},
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                            done=False,
                        )
                    ).encode()
                    continue

                if message is None:
                    break

                yield message.encode()

                if is_disconnected is not None and await is_disconnected():
                    break
        finally:
            await self.unregister(connection)

    def _build_message(self, event: AgentStreamEvent) -> SseEventMessage:
        payload = json.dumps(
            event.model_dump(by_alias=True, exclude_none=True),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return SseEventMessage(
            event=event.event_type,
            id=event.event_id,
            retry=self._retry_ms,
            data=payload,
        )


def _normalize_user_id(user_id: str | None) -> str | None:
    if not isinstance(user_id, str):
        return None
    candidate = user_id.strip()
    return candidate or None


def _missing_message(
    *,
    session_id: str,
    user_id: str | None,
    page_id: str | None,
) -> str:
    user_label = _normalize_user_id(user_id)
    parts = []
    if user_label is not None:
        parts.append(f"user_id={user_label}")
    parts.append(f"session_id={session_id}")
    if page_id is not None:
        parts.append(f"page_id={page_id}")
    return f"SSE connection not found: {', '.join(parts)}"
