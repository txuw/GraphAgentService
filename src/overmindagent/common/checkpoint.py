from __future__ import annotations

from typing import Any, Protocol

from langgraph.checkpoint.memory import InMemorySaver

from .config import GraphSettings


class CheckpointProvider(Protocol):
    def build(self) -> Any | None:
        ...


class InMemoryCheckpointProvider:
    def __init__(self) -> None:
        self._checkpointer = InMemorySaver()

    def build(self) -> Any:
        return self._checkpointer


class DisabledCheckpointProvider:
    def build(self) -> None:
        return None


def create_checkpoint_provider(settings: GraphSettings) -> CheckpointProvider:
    if settings.checkpoint_mode == "memory":
        return InMemoryCheckpointProvider()

    return DisabledCheckpointProvider()
