from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.tools import tool

_CITY_TIMEZONES = {
    "beijing": "Asia/Shanghai",
    "hangzhou": "Asia/Shanghai",
    "shanghai": "Asia/Shanghai",
    "shenzhen": "Asia/Shanghai",
    "san francisco": "America/Los_Angeles",
}


@tool
def lookup_local_time(city: str) -> str:
    """Look up the current local time for a supported city."""
    normalized = city.strip().lower()
    timezone_name = _CITY_TIMEZONES.get(normalized)
    if timezone_name is None:
        return f"No timezone mapping is configured for {city}."

    current_time = datetime.now(ZoneInfo(timezone_name)).strftime("%Y-%m-%d %H:%M:%S")
    return f"The local time in {city} is {current_time} ({timezone_name})."
