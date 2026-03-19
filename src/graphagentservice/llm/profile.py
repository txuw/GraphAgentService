from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


def as_mapping(value: Any) -> Mapping[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return value

    items = getattr(value, "items", None)
    if callable(items):
        return dict(items())

    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()

    return {}


@dataclass(frozen=True, slots=True)
class LLMProfile:
    name: str
    provider: str = "openai"
    api_key: Any = None
    base_url: str | None = None
    model: str = "gpt-4o-mini"
    temperature: float | None = 0.0
    timeout: float = 60.0
    max_tokens: int | None = None
    provider_options: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, name: str, raw_settings: Any) -> LLMProfile:
        settings = as_mapping(raw_settings)
        return cls(
            name=name,
            provider=str(settings.get("provider", "openai")),
            api_key=settings.get("api_key"),
            base_url=settings.get("base_url"),
            model=str(settings.get("model", "gpt-4o-mini")),
            temperature=settings.get("temperature", 0.0),
            timeout=float(settings.get("timeout", 60.0)),
            max_tokens=settings.get("max_tokens"),
            provider_options=dict(as_mapping(settings.get("provider_options"))),
        )
