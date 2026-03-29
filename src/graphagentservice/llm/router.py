from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from graphagentservice.common.config import LLMSettings

from .factory import ChatModelFactory
from .profile import LLMProfile, as_mapping


class UnknownLLMProfileError(KeyError):
    pass


class LLMRouter:
    def __init__(
        self,
        settings: LLMSettings,
        factory: ChatModelFactory | None = None,
    ) -> None:
        self._factory = factory or ChatModelFactory()
        self._profiles = self._load_profiles(settings)
        self._aliases = self._load_aliases(settings)
        self._default_profile_name = self._resolve_default_profile_name(settings)

    def list_profiles(self) -> tuple[str, ...]:
        return tuple(self._profiles.keys())

    def resolve_profile(self, profile: str | None = None) -> LLMProfile:
        profile_name = self._resolve_profile_name(profile)
        try:
            return self._profiles[profile_name]
        except KeyError as exc:
            raise UnknownLLMProfileError(f"Unknown llm profile: {profile_name}") from exc

    def create_model(
        self,
        *,
        profile: str | None = None,
        tags: Sequence[str] = (),
        metadata: Mapping[str, Any] | None = None,
        default_headers: Mapping[str, str] | None = None,
    ):
        resolved_profile = self.resolve_profile(profile)
        model = self._factory.create(
            resolved_profile,
            default_headers=default_headers,
        )
        if tags or metadata:
            model = model.with_config(
                tags=list(tags),
                metadata=dict(metadata or {}),
            )
        return model

    def _resolve_default_profile_name(self, settings: LLMSettings) -> str:
        configured_name = as_mapping(settings).get("default_profile")
        if configured_name is None:
            configured_name = "default" if "default" in self._profiles else next(iter(self._profiles))
        return self._resolve_profile_name(str(configured_name))

    def _resolve_profile_name(self, profile: str | None) -> str:
        candidate = profile or self._default_profile_name
        return self._aliases.get(candidate, candidate)

    @staticmethod
    def _load_profiles(settings: LLMSettings) -> dict[str, LLMProfile]:
        raw_settings = as_mapping(settings)
        raw_profiles = as_mapping(raw_settings.get("profiles"))
        if not raw_profiles:
            return {"default": LLMProfile.from_mapping("default", raw_settings)}

        return {
            profile_name: LLMProfile.from_mapping(profile_name, profile_settings)
            for profile_name, profile_settings in raw_profiles.items()
        }

    @staticmethod
    def _load_aliases(settings: LLMSettings) -> dict[str, str]:
        raw_settings = as_mapping(settings)
        raw_aliases = as_mapping(raw_settings.get("aliases") or raw_settings.get("bindings"))
        return {
            str(alias_name): str(profile_name)
            for alias_name, profile_name in raw_aliases.items()
        }
