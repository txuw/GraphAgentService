from __future__ import annotations

from langchain_core.tools import tool

_WEATHER_SNAPSHOTS = {
    "beijing": "Beijing weather is sunny, 26C, with light wind.",
    "hangzhou": "Hangzhou weather is cloudy, 24C, with possible light rain.",
    "shanghai": "Shanghai weather is cloudy, 25C, with humid air.",
    "shenzhen": "Shenzhen weather is warm, 29C, with scattered clouds.",
}


@tool
def lookup_weather(location: str) -> str:
    """Look up a canned weather snapshot for a city."""
    normalized = location.strip().lower()
    if not normalized:
        return "No location was provided."
    return _WEATHER_SNAPSHOTS.get(
        normalized,
        f"No weather snapshot is configured for {location}.",
    )
