from __future__ import annotations

import asyncio
import contextvars
import logging
import logging.config
import os
import threading
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Per-request context variables
# ---------------------------------------------------------------------------

_trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")
_otel_trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("otel_trace_id", default="")


def set_log_trace_id(trace_id: str) -> contextvars.Token[str]:
    """Set the trace-id for the current async task / thread context.

    Returns a token that can be passed to :func:`reset_log_trace_id` to restore
    the previous value (useful in ASGI middleware for keep-alive connections).
    """
    return _trace_id_var.set(trace_id)


def reset_log_trace_id(token: contextvars.Token[str]) -> None:
    """Reset the trace-id to the value it had before the matching :func:`set_log_trace_id` call."""
    _trace_id_var.reset(token)


def get_log_trace_id() -> str:
    """Return the trace-id bound to the current context, or an empty string."""
    return _trace_id_var.get()


# ---------------------------------------------------------------------------
# Logging filter – injects context fields into every LogRecord
# ---------------------------------------------------------------------------

class ContextFilter(logging.Filter):
    """Inject ``trace_id`` and ``otel_trace_id`` from contextvars into every LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = _trace_id_var.get() or "-"
        record.otel_trace_id = _otel_trace_id_var.get() or "-"
        return True


# ---------------------------------------------------------------------------
# Spring-style formatter
# ---------------------------------------------------------------------------

_PID = os.getpid()

# Map Python level names to 5-char Spring-style abbreviations
_LEVEL_LABELS: dict[str, str] = {
    "DEBUG": "DEBUG",
    "INFO": " INFO",
    "WARNING": " WARN",
    "ERROR": "ERROR",
    "CRITICAL": "CRIT!",
}


def _thread_display(width: int = 15) -> str:
    """Return a thread/task label, right-justified and truncated to *width* chars."""
    try:
        task = asyncio.current_task()
        name = task.get_name() if task is not None else threading.current_thread().name
    except RuntimeError:
        name = threading.current_thread().name
    # Logback %15.15t: right-justify, truncate from the left when longer
    if len(name) <= width:
        return name.rjust(width)
    return name[-width:]


def _abbreviate_logger(name: str, max_length: int = 36) -> str:
    """Abbreviate dotted package segments (all but the last) to one character each."""
    if len(name) <= max_length:
        return name
    parts = name.split(".")
    short = ".".join(p[0] if i < len(parts) - 1 else p for i, p in enumerate(parts))
    return short[:max_length]


class SpringStyleFormatter(logging.Formatter):
    """Formats log records to match the reference Spring Boot console pattern.

    Reference::

        %d{yyyy-MM-dd'T'HH:mm:ss.SSSXXX} %5p traceId=%X{requestTraceId:-}
        otelTraceId=%X{traceId:-} ${PID:- } --- [%15.15t] %logger{36} : %msg%n

    Example output::

        2026-03-29T14:30:45.123+08:00  INFO traceId=abc otelTraceId=- 12345 ---
        [         Task-1] g.s.graph_service            : Graph invoke started
    """

    def format(self, record: logging.LogRecord) -> str:
        # ISO-8601 timestamp with milliseconds and timezone offset (+08:00 style)
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).astimezone()
        ts = ts.replace(microsecond=int(record.msecs) * 1000)
        timestamp = ts.isoformat(timespec="milliseconds")

        level = _LEVEL_LABELS.get(record.levelname, record.levelname[:5].rjust(5))
        trace_id = getattr(record, "trace_id", "-") or "-"
        otel_trace_id = getattr(record, "otel_trace_id", "-") or "-"
        thread = _thread_display(15)
        logger_name = _abbreviate_logger(record.name, 36)

        message = record.getMessage()
        if record.exc_info:
            if not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            message = f"{message}\n{record.exc_text}"
        if record.stack_info:
            message = f"{message}\n{self.formatStack(record.stack_info)}"

        return (
            f"{timestamp} {level} "
            f"traceId={trace_id} otelTraceId={otel_trace_id} "
            f"{_PID} --- [{thread}] "
            f"{logger_name:<36} : {message}"
        )


# ---------------------------------------------------------------------------
# Bootstrap helpers
# ---------------------------------------------------------------------------

def build_log_config(log_level: str = "INFO") -> dict[str, Any]:
    """Return a ``logging.config.dictConfig``-compatible configuration dict.

    Suitable for passing directly to ``uvicorn.run(log_config=...)``.
    The config replaces uvicorn's default handler/formatter and routes all
    ``uvicorn.*`` loggers through our :class:`SpringStyleFormatter`.
    """
    level = log_level.upper()
    formatter_path = f"{__name__}.SpringStyleFormatter"
    filter_path = f"{__name__}.ContextFilter"
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "trace_context": {"()": filter_path},
        },
        "formatters": {
            "spring": {"()": formatter_path},
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "spring",
                "filters": ["trace_context"],
                "stream": "ext://sys.stdout",
            }
        },
        "loggers": {
            "uvicorn": {
                "handlers": ["console"],
                "level": level,
                "propagate": False,
            },
            "uvicorn.error": {
                "handlers": ["console"],
                "level": level,
                "propagate": False,
            },
            "uvicorn.access": {
                "handlers": ["console"],
                "level": level,
                "propagate": False,
            },
        },
        "root": {
            "level": level,
            "handlers": ["console"],
        },
    }


def configure_logging(settings: Any) -> None:
    """Configure application-wide logging from *settings*.

    Intended for use outside Uvicorn (e.g. tests, scripts).  When starting
    via Uvicorn, pass the result of :func:`build_log_config` as ``log_config``
    to ``uvicorn.run()`` instead, so Uvicorn applies our config at start-up.
    """
    log_level = str(getattr(getattr(settings, "app", None), "log_level", "INFO"))
    logging.config.dictConfig(build_log_config(log_level))
