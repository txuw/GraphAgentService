from __future__ import annotations

from langchain_core.tools import BaseTool

from .math import calculate
from .time import lookup_local_time
from .weather import lookup_weather


def build_toolset() -> list[BaseTool]:
    return [lookup_weather, lookup_local_time, calculate]
