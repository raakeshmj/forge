"""Structured JSON logging with a per-context correlation id.

Log records carry metadata only. Callers must not log secrets, raw prompts,
or tool payloads (CLAUDE.md §15).
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

correlation_id: ContextVar[str | None] = ContextVar("forge_correlation_id", default=None)

# Attributes every LogRecord has; anything else was passed via `extra=` and is emitted as a field.
_RESERVED_ATTRS = frozenset(logging.LogRecord("", logging.INFO, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
    "taskName",
    "color_message",  # uvicorn duplicates `message` with ANSI escapes
}


class CorrelationIdFilter(logging.Filter):
    """Stamp the current correlation id onto records while still in the emitting context."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "correlation_id"):
            current = correlation_id.get()
            if current is not None:
                record.correlation_id = current
        return True


class JsonFormatter(logging.Formatter):
    """Render each record as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED_ATTRS and not key.startswith("_"):
                entry[key] = value
        if record.exc_info and record.exc_info[0] is not None:
            entry["error_type"] = record.exc_info[0].__qualname__
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(level: str) -> None:
    """Route all logging, including uvicorn's, through a single JSON handler on stdout."""
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(CorrelationIdFilter())
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
