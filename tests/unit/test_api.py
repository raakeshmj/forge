import asyncio
import logging
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from forge.api.app import create_app
from forge.api.correlation import CORRELATION_HEADER
from forge.domain.repositories import UnitOfWork
from forge.health import DependencyCheck, DependencyUnavailableError
from forge.infrastructure.clock import SystemClock
from forge.observability.logs import CorrelationIdFilter


class FakeCheck:
    def __init__(self, name: str, behaviour: Callable[[], None] | None = None) -> None:
        self.name = name
        self._behaviour = behaviour

    async def check(self) -> None:
        if self._behaviour is not None:
            self._behaviour()


class HangingCheck:
    name = "slow"

    async def check(self) -> None:
        await asyncio.sleep(10)


def _unavailable(name: str) -> Callable[[], None]:
    def fail() -> None:
        try:
            raise ConnectionRefusedError("connection refused")
        except ConnectionRefusedError as exc:
            raise DependencyUnavailableError(name) from exc

    return fail


def _no_database() -> UnitOfWork:
    raise AssertionError("health endpoints must not touch the database")


def _app(*checks: DependencyCheck, timeout: float = 1.0, **kwargs: Any) -> FastAPI:
    return create_app(
        uow_factory=_no_database,
        clock=SystemClock(),
        readiness_checks=checks,
        readiness_timeout_seconds=timeout,
        **kwargs,
    )


def _client(*checks: DependencyCheck, timeout: float = 1.0) -> TestClient:
    return TestClient(_app(*checks, timeout=timeout))


def test_liveness_reports_ok_without_consulting_dependencies() -> None:
    response = _client(FakeCheck("postgres", _unavailable("postgres"))).get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_is_ok_when_every_dependency_answers() -> None:
    response = _client(FakeCheck("postgres"), FakeCheck("redis")).get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"postgres": {"status": "ok"}, "redis": {"status": "ok"}},
    }


def test_readiness_reports_the_failing_dependency_and_its_cause_type() -> None:
    response = _client(FakeCheck("postgres"), FakeCheck("redis", _unavailable("redis"))).get(
        "/health/ready"
    )

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {
            "postgres": {"status": "ok"},
            "redis": {"status": "unavailable", "error_type": "ConnectionRefusedError"},
        },
    }


def test_readiness_times_out_a_hanging_dependency() -> None:
    response = _client(HangingCheck(), timeout=0.05).get("/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["slow"] == {
        "status": "unavailable",
        "error_type": "TimeoutError",
    }


def test_unexpected_check_errors_are_not_reported_as_dependency_state() -> None:
    def bug() -> None:
        raise KeyError("programming error")

    with pytest.raises(KeyError):
        _client(FakeCheck("postgres", bug)).get("/health/ready")


def test_shutdown_releases_resources() -> None:
    released: list[str] = []

    async def release() -> None:
        released.append("engine")

    app = _app(on_shutdown=(release,))
    with TestClient(app):
        assert released == []

    assert released == ["engine"]


def test_valid_inbound_correlation_id_is_echoed() -> None:
    response = _client().get("/health/live", headers={CORRELATION_HEADER: "abc-123"})

    assert response.headers[CORRELATION_HEADER] == "abc-123"


def test_unsafe_inbound_correlation_id_is_replaced() -> None:
    response = _client().get("/health/live", headers={CORRELATION_HEADER: "bad id\twith spaces"})

    issued = response.headers[CORRELATION_HEADER]
    assert issued != "bad id\twith spaces"
    uuid.UUID(issued)


def test_request_is_logged_with_its_correlation_id_and_without_query_string(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.handler.addFilter(CorrelationIdFilter())
    with caplog.at_level(logging.INFO, logger="forge.api.request"):
        _client().get("/health/live?token=secret", headers={CORRELATION_HEADER: "req-9"})

    (record,) = [r for r in caplog.records if r.name == "forge.api.request"]
    assert record.__dict__["correlation_id"] == "req-9"
    assert record.__dict__["path"] == "/health/live"
    assert record.__dict__["status_code"] == 200
    assert "secret" not in record.getMessage()
