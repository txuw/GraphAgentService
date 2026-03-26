from __future__ import annotations

from datetime import datetime
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from langchain_core.tools import tool

_DEFAULT_TIMEZONE = "Asia/Shanghai"


def _normalize_timezone(timezone_name: object) -> str:
    if timezone_name is None:
        return ""
    return str(timezone_name).strip()


@tool
def lookup_local_time(
    timezone: Annotated[
        str | None,
        (
            "Optional IANA time zone name in ZoneInfo format, written as "
            "'Area/Location'. Provide this only when the user explicitly "
            "identifies a region, city, or time zone and it can be mapped "
            "confidently to a single IANA time zone. If the user asks for the "
            "current or local time without specifying a region, omit this "
            "argument and let the tool use its default time zone "
            "'Asia/Shanghai'."
        ),
    ] = None,
) -> str:
    """Look up the current local time using an IANA ZoneInfo time zone.

    Accepts a time zone in IANA ZoneInfo format, written as 'Area/Location'.
    If the user specifies a region, city, or time zone, convert it to the
    matching IANA time zone before calling this tool.
    If the user asks for the current or local time without specifying a
    region, call this tool without the `timezone` argument. In that case,
    the tool defaults to 'Asia/Shanghai'.
    """
    resolved_timezone = _normalize_timezone(timezone) or _DEFAULT_TIMEZONE
    try:
        zone = ZoneInfo(resolved_timezone)
    except ZoneInfoNotFoundError:
        return f"Unknown IANA timezone: {resolved_timezone}."

    current_time = datetime.now(zone).strftime("%Y-%m-%d %H:%M:%S")
    return f"The local time in {resolved_timezone} is {current_time} ({resolved_timezone})."
