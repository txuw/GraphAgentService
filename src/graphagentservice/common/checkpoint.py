from __future__ import annotations

import asyncio
import sys
from contextlib import suppress
from typing import Any, Protocol

from langgraph.checkpoint.memory import InMemorySaver

from .config import GraphSettings


class CheckpointConfigurationError(RuntimeError):
    pass


class CheckpointProvider(Protocol):
    def build(self) -> Any | None:
        ...

    async def startup(self) -> None:
        ...

    async def shutdown(self) -> None:
        ...


class InMemoryCheckpointProvider:
    def __init__(self) -> None:
        self._checkpointer = InMemorySaver()

    def build(self) -> Any:
        return self._checkpointer

    async def startup(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None


class PostgresCheckpointProvider:
    def __init__(self, *, connection_string: str) -> None:
        candidate = connection_string.strip()
        if not candidate:
            raise CheckpointConfigurationError(
                "graph.checkpoint.postgres.url is required when checkpoint_mode=postgres"
            )
        self._connection_string = candidate
        self._context_manager: Any | None = None
        self._checkpointer: Any | None = None
        self._started = False

    def build(self) -> Any | None:
        return self._checkpointer

    async def startup(self) -> None:
        if self._started:
            return

        _ensure_windows_selector_event_loop_policy()
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        except ImportError as exc:
            raise CheckpointConfigurationError(
                "langgraph-checkpoint-postgres is required when checkpoint_mode=postgres"
            ) from exc

        self._context_manager = AsyncPostgresSaver.from_conn_string(
            self._connection_string
        )
        self._checkpointer = await self._context_manager.__aenter__()
        try:
            await self._checkpointer.aget_tuple(
                {
                    "configurable": {
                        "thread_id": "__checkpoint_healthcheck__",
                        "checkpoint_ns": "__checkpoint_healthcheck__",
                    }
                }
            )
        except Exception:
            with suppress(Exception):
                await self._context_manager.__aexit__(None, None, None)
            self._context_manager = None
            self._checkpointer = None
            raise
        self._started = True

    async def shutdown(self) -> None:
        if not self._started:
            return

        if self._context_manager is not None:
            with suppress(Exception):
                await self._context_manager.__aexit__(None, None, None)
        self._context_manager = None
        self._checkpointer = None
        self._started = False


class DisabledCheckpointProvider:
    def build(self) -> None:
        return None

    async def startup(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None


def create_checkpoint_provider(settings: GraphSettings) -> CheckpointProvider:
    checkpoint_mode = str(settings.get("checkpoint_mode", "disabled")).strip().lower()
    postgres_modes = {"postgres", "postgresql", "pg"}

    if checkpoint_mode == "memory":
        return InMemoryCheckpointProvider()

    if checkpoint_mode in postgres_modes:
        _ensure_windows_selector_event_loop_policy()
        connection_string = _read_nested_setting(settings, "checkpoint", "postgres", "url")
        if not isinstance(connection_string, str) or not connection_string.strip():
            raise CheckpointConfigurationError(
                "graph.checkpoint.postgres.url is required when checkpoint_mode=postgres"
            )
        return PostgresCheckpointProvider(connection_string=connection_string)

    if checkpoint_mode in {"disabled", "none", "off"}:
        return DisabledCheckpointProvider()

    raise CheckpointConfigurationError(
        f"Unsupported graph.checkpoint_mode: {checkpoint_mode}"
    )


def _read_nested_setting(settings: Any, *parts: str) -> Any:
    current = settings
    for part in parts:
        if not hasattr(current, "get"):
            return None
        current = current.get(part)
        if current is None:
            return None
    return current


def _ensure_windows_selector_event_loop_policy() -> None:
    if sys.platform != "win32":
        return
    selector_policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if selector_policy is None:
        return
    current_policy = asyncio.get_event_loop_policy()
    if isinstance(current_policy, selector_policy):
        return
    asyncio.set_event_loop_policy(selector_policy())
