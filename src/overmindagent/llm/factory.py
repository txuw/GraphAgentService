from __future__ import annotations

from overmindagent.common.config import LLMSettings

from .adapters import (
    OpenAIChatAdapter,
    OpenAIResponsesAdapter,
    ProviderAdapter,
    UnsupportedLLMConfigurationError,
)
from .session import LLMSession
from .tools import ToolRegistry


class LLMSessionFactory:
    def __init__(self, settings: LLMSettings) -> None:
        self._settings = settings
        self._adapters: dict[tuple[str, str], ProviderAdapter] = {}
        self.register(OpenAIResponsesAdapter())
        self.register(OpenAIChatAdapter())

    def register(self, adapter: ProviderAdapter) -> None:
        # Provider and protocol together define a stable external session shape.
        self._adapters[(adapter.provider_name, adapter.protocol_name)] = adapter

    def create(self, tool_registry: ToolRegistry | None = None) -> LLMSession:
        provider = self._settings.get("provider", "openai")
        protocol = self._settings.get("protocol", "responses")
        adapter = self._adapters.get((provider, protocol))
        if adapter is None:
            raise UnsupportedLLMConfigurationError(
                f"Unsupported provider/protocol combination: "
                f"{provider}/{protocol}"
            )
        return adapter.build_session(settings=self._settings, tool_registry=tool_registry)
