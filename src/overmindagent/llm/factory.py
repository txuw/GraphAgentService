from __future__ import annotations

from typing import TypeVar

from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from overmindagent.common.config import LLMSettings

StructuredSchema = TypeVar("StructuredSchema", bound=BaseModel)


class MissingLLMConfigurationError(RuntimeError):
    pass


class LLMModelFactory:
    def __init__(self, settings: LLMSettings) -> None:
        self._settings = settings

    def create_chat_model(self) -> ChatOpenAI:
        api_key = self._settings.api_key.get_secret_value() if self._settings.api_key else None
        if not api_key:
            raise MissingLLMConfigurationError(
                "Missing OVERMIND_LLM_API_KEY for LangGraph model invocation."
            )

        kwargs: dict[str, object] = {
            "api_key": api_key,
            "model": self._settings.model,
            "temperature": self._settings.temperature,
            "timeout": self._settings.timeout,
        }
        if self._settings.base_url:
            kwargs["base_url"] = self._settings.base_url
        if self._settings.max_tokens is not None:
            kwargs["max_tokens"] = self._settings.max_tokens

        return ChatOpenAI(**kwargs)

    def create_structured_model(self, schema: type[StructuredSchema]):
        return self.create_chat_model().with_structured_output(schema)
