from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from dynaconf import Dynaconf
from dynaconf.utils.boxing import DynaBox
from dotenv import dotenv_values

Settings = Dynaconf
AppSettings = DynaBox
LLMSettings = DynaBox
GraphSettings = DynaBox
ObservabilitySettings = DynaBox


def _parse_env_value(value: str) -> Any:
    candidate = value.strip()
    if not candidate:
        return value

    try:
        return tomllib.loads(f"value = {candidate}")["value"]
    except tomllib.TOMLDecodeError:
        return value


def _build_nested_overrides(values: Mapping[str, str | None]) -> dict[str, Any]:
    data: dict[str, Any] = {}

    for key, raw_value in values.items():
        if raw_value is None or "__" not in key:
            continue

        path = [part.lower() for part in key.split("__") if part]
        if not path:
            continue

        current = data
        for part in path[:-1]:
            current = current.setdefault(part, {})
        current[path[-1]] = _parse_env_value(raw_value)

    return data


@lru_cache
def get_settings() -> Settings:
    root = Path.cwd()
    settings = Dynaconf(
        settings_files=[str(root / "settings.yaml")],
        envvar_prefix=False,
        environments=False,
        merge_enabled=True,
        lowercase_read=True,
        nested_separator="__",
    )
    settings.update(_build_nested_overrides(dotenv_values(root / ".env")), merge=True)
    settings.update(_build_nested_overrides(os.environ), merge=True)
    return settings
