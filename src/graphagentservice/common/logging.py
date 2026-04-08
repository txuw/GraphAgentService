from __future__ import annotations

import contextlib
import contextvars
import logging
import logging.config
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

_LOG_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "graphagentservice_log_context",
    default={},
)

_DEFAULT_CONTEXT: dict[str, str] = {
    "traceId": "-",
    "otelTraceId": "-",
    "graph": "-",
    "sessionId": "-",
    "requestId": "-",
    "pageId": "-",
    "userId": "-",
    "node": "-",
    "tool": "-",
    "event": "-",
    "status": "-",
    "errorType": "-",
    "errorCode": "-",
}

_RESERVED_ATTRS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
    "taskName",
}


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        values = dict(_DEFAULT_CONTEXT)
        values.update(_LOG_CONTEXT.get())

        for key, value in values.items():
            current = getattr(record, key, None)
            if current not in (None, ""):
                setattr(record, key, _stringify(current))
                continue
            setattr(record, key, _stringify(value))

        if not hasattr(record, "elapsedMs"):
            record.elapsedMs = "-"
        else:
            record.elapsedMs = _stringify(getattr(record, "elapsedMs"))

        if not hasattr(record, "chunkCount"):
            record.chunkCount = "-"
        else:
            record.chunkCount = _stringify(getattr(record, "chunkCount"))

        return True


class KeyValueFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        timestamp = datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds")
        task_name = getattr(record, "taskName", None) or getattr(record, "threadName", "MainThread")
        prefix = (
            f"{timestamp}  {record.levelname:<5} "
            f"traceId={_stringify(getattr(record, 'traceId', '-'))} "
            f"otelTraceId={_stringify(getattr(record, 'otelTraceId', '-'))} "
            f"{record.process} --- [{task_name}] "
            f"{record.name:<36} : {message}"
        )
        extras = self._build_extras(record)
        if extras:
            prefix = f"{prefix} {' '.join(extras)}"
        if record.exc_info:
            prefix = f"{prefix}\n{self.formatException(record.exc_info)}"
        return prefix

    def _build_extras(self, record: logging.LogRecord) -> list[str]:
        values: list[str] = []
        for key, value in record.__dict__.items():
            if key in _RESERVED_ATTRS or key.startswith("_"):
                continue
            if key in {"traceId", "otelTraceId"}:
                continue
            rendered = _stringify(value)
            if rendered in {"", "-"}:
                continue
            values.append(f"{key}={rendered}")
        values.sort()
        return values


def configure_logging(settings: Any) -> None:
    logging.config.dictConfig(build_log_config(settings))


def build_log_config(settings: Any) -> dict[str, Any]:
    log_level = str(settings.app.log_level).upper()
    access_log = bool(settings.get("observability.logging.access_log", True))
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "context": {
                "()": "graphagentservice.common.logging.ContextFilter",
            }
        },
        "formatters": {
            "structured": {
                "()": "graphagentservice.common.logging.KeyValueFormatter",
            }
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "structured",
                "filters": ["context"],
            }
        },
        "root": {
            "level": log_level,
            "handlers": ["default"],
        },
        "loggers": {
            "uvicorn": {"level": log_level, "handlers": ["default"], "propagate": False},
            "uvicorn.error": {"level": log_level, "handlers": ["default"], "propagate": False},
            "uvicorn.access": {
                "level": "INFO" if access_log else "CRITICAL",
                "handlers": ["default"],
                "propagate": False,
            },
        },
    }


def bind_log_context(**values: Any) -> contextvars.Token[dict[str, Any]]:
    context = dict(_LOG_CONTEXT.get())
    context.update({key: value for key, value in values.items() if value is not None})
    return _LOG_CONTEXT.set(context)


def reset_log_context(token: contextvars.Token[dict[str, Any]]) -> None:
    _LOG_CONTEXT.reset(token)


@contextlib.contextmanager
def log_context(**values: Any) -> Iterator[None]:
    token = bind_log_context(**values)
    try:
        yield
    finally:
        reset_log_context(token)


@contextlib.contextmanager
def log_timing(logger: logging.Logger, event: str, **values: Any) -> Iterator[None]:
    started = perf_counter()
    logger.info("Operation started", extra={"event": event, "status": "started", **values})
    try:
        yield
    except Exception:
        elapsed_ms = round((perf_counter() - started) * 1000)
        logger.exception(
            "Operation failed",
            extra={
                "event": event,
                "status": "failed",
                "elapsedMs": elapsed_ms,
                "errorType": "exception",
                **values,
            },
        )
        raise
    elapsed_ms = round((perf_counter() - started) * 1000)
    logger.info(
        "Operation completed",
        extra={"event": event, "status": "completed", "elapsedMs": elapsed_ms, **values},
    )


def current_log_context() -> dict[str, Any]:
    return dict(_LOG_CONTEXT.get())


def context_extra(**values: Any) -> dict[str, Any]:
    extra = dict(current_log_context())
    extra.update(values)
    return extra


def mask_value(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return str(value).lower()
    return _stringify(value)


def summarize_mapping_keys(value: Mapping[str, Any] | None) -> str:
    if not value:
        return "-"
    return ",".join(sorted(str(key) for key in value.keys()))


def _stringify(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, float):
        return f"{value:.0f}" if value.is_integer() else f"{value:.3f}"
    return str(value)
