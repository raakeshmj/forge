import logging
import uuid

import pytest
from fastapi.testclient import TestClient

from forge.api.app import create_app
from forge.api.correlation import CORRELATION_HEADER
from forge.observability.logs import CorrelationIdFilter


def test_liveness_reports_ok() -> None:
    response = TestClient(create_app()).get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_valid_inbound_correlation_id_is_echoed() -> None:
    response = TestClient(create_app()).get("/health/live", headers={CORRELATION_HEADER: "abc-123"})

    assert response.headers[CORRELATION_HEADER] == "abc-123"


def test_unsafe_inbound_correlation_id_is_replaced() -> None:
    response = TestClient(create_app()).get(
        "/health/live", headers={CORRELATION_HEADER: "bad id\twith spaces"}
    )

    issued = response.headers[CORRELATION_HEADER]
    assert issued != "bad id\twith spaces"
    uuid.UUID(issued)


def test_request_is_logged_with_its_correlation_id_and_without_query_string(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.handler.addFilter(CorrelationIdFilter())
    with caplog.at_level(logging.INFO, logger="forge.api.request"):
        TestClient(create_app()).get(
            "/health/live?token=secret", headers={CORRELATION_HEADER: "req-9"}
        )

    (record,) = [r for r in caplog.records if r.name == "forge.api.request"]
    assert record.__dict__["correlation_id"] == "req-9"
    assert record.__dict__["path"] == "/health/live"
    assert record.__dict__["status_code"] == 200
    assert "secret" not in record.getMessage()
