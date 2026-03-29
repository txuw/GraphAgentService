from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from .profile import LLMProfile

ChatModelBuilder = Callable[[LLMProfile, Mapping[str, str] | None], BaseChatModel]


class ChatModelBuildError(RuntimeError):
    pass


class ChatModelFactory:
    def __init__(self) -> None:
        self._builders: dict[str, ChatModelBuilder] = {}
        self.register("openai", self._build_openai_model)

    def register(self, provider_name: str, builder: ChatModelBuilder) -> None:
        self._builders[provider_name] = builder

    def create(
        self,
        profile: LLMProfile,
        *,
        default_headers: Mapping[str, str] | None = None,
    ) -> BaseChatModel:
        builder = self._builders.get(profile.provider)
        if builder is None:
            raise ChatModelBuildError(f"Unsupported llm provider: {profile.provider}")
        return builder(profile, default_headers)

    @staticmethod
    def _build_openai_model(
        profile: LLMProfile,
        default_headers: Mapping[str, str] | None = None,
    ) -> BaseChatModel:
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise ChatModelBuildError(
                "The langchain-openai package is required. Run `uv sync` to install dependencies."
            ) from exc

        kwargs: dict[str, Any] = {
            "model": profile.model,
            "timeout": profile.timeout,
        }
        api_key = _resolve_secret(profile.api_key)
        if api_key is not None:
            kwargs["api_key"] = api_key
        if profile.base_url:
            kwargs["base_url"] = profile.base_url
        if default_headers:
            kwargs["default_headers"] = dict(default_headers)
        if profile.temperature is not None:
            kwargs["temperature"] = profile.temperature
        if profile.max_tokens is not None:
            kwargs["max_completion_tokens"] = profile.max_tokens
        if profile.provider_options:
            kwargs["model_kwargs"] = dict(profile.provider_options)

        return ChatOpenAI(**kwargs)


def _resolve_secret(value: Any) -> str | None:
    if value is None:
        return None

    getter = getattr(value, "get_secret_value", None)
    if callable(getter):
        return getter()

    return str(value)
