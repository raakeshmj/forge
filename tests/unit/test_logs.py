import json
import logging
import sys

from forge.observability.logs import CorrelationIdFilter, JsonFormatter, correlation_id


def _record(msg: str, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord("forge.test", logging.WARNING, __file__, 1, msg, None, None)
    record.__dict__.update(extra)
    return record


def test_record_renders_as_single_line_json_with_extra_fields() -> None:
    line = JsonFormatter().format(_record("checkpoint skipped", operation="resume", attempt=2))

    assert "\n" not in line
    entry = json.loads(line)
    assert entry["level"] == "WARNING"
    assert entry["logger"] == "forge.test"
    assert entry["message"] == "checkpoint skipped"
    assert entry["operation"] == "resume"
    assert entry["attempt"] == 2
    assert "correlation_id" not in entry


def test_correlation_id_is_stamped_in_the_emitting_context_not_at_format_time() -> None:
    record = _record("hello")
    token = correlation_id.set("req-123")
    try:
        CorrelationIdFilter().filter(record)
    finally:
        correlation_id.reset(token)

    entry = json.loads(JsonFormatter().format(record))
    assert entry["correlation_id"] == "req-123"


def test_exception_type_is_recorded() -> None:
    try:
        raise TimeoutError("redis ping timed out")
    except TimeoutError:
        record = logging.LogRecord(
            "forge.test", logging.ERROR, __file__, 1, "check failed", None, sys.exc_info()
        )

    entry = json.loads(JsonFormatter().format(record))
    assert entry["error_type"] == "TimeoutError"
    assert "redis ping timed out" in entry["exception"]


def test_explicit_error_type_is_not_overwritten_by_exc_info() -> None:
    try:
        raise TimeoutError("wrapper")
    except TimeoutError:
        record = logging.LogRecord(
            "forge.test", logging.WARNING, __file__, 1, "check failed", None, sys.exc_info()
        )
    record.error_type = "ConnectionRefusedError"

    entry = json.loads(JsonFormatter().format(record))
    assert entry["error_type"] == "ConnectionRefusedError"
