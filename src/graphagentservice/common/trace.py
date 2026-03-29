from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import uuid4

TRACE_ID_HEADER = "X-Trace-Id"


@dataclass(frozen=True, slots=True)
class RequestTraceContext:
    trace_id: str
    request_headers: dict[str, str]


def resolve_request_trace_context(headers: Mapping[str, str]) -> RequestTraceContext:
    trace_id = _find_header(
        headers=headers,
        header_name=TRACE_ID_HEADER,
    )
    resolved_trace_id = trace_id.strip() if trace_id is not None else ""
    if not resolved_trace_id:
        resolved_trace_id = uuid4().hex

    normalized_headers = {
        str(key): str(value)
        for key, value in headers.items()
        if key and value is not None and str(key).lower() != TRACE_ID_HEADER.lower()
    }
    normalized_headers[TRACE_ID_HEADER] = resolved_trace_id
    return RequestTraceContext(
        trace_id=resolved_trace_id,
        request_headers=normalized_headers,
    )


def build_trace_response_headers(trace_id: str) -> dict[str, str]:
    return {TRACE_ID_HEADER: trace_id}


def _find_header(
    *,
    headers: Mapping[str, str],
    header_name: str,
) -> str | None:
    target_name = header_name.lower()
    for key, value in headers.items():
        if str(key).lower() == target_name and value is not None:
            return str(value)
    return None
