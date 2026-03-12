from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from .schemas import LLMEvent, LLMRequest, LLMResponse


class LLMSession(Protocol):
    async def invoke(self, request: LLMRequest) -> LLMResponse:
        ...

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMEvent]:
        ...
