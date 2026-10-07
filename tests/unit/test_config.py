import pytest

from forge.config import ApiSettings, ConfigError, WorkerSettings

DATABASE = {"FORGE_DATABASE_URL": "postgresql+psycopg://forge:hunter2@db:5432/forge"}
API_REQUIRED = {**DATABASE, "FORGE_REDIS_URL": "redis://:hunter2@redis:6379/0"}


def test_api_defaults_apply_when_optional_variables_are_absent() -> None:
    settings = ApiSettings.from_env(API_REQUIRED)

    assert settings.log_level == "INFO"
    assert settings.otel_exporter == "none"
    assert settings.api_host == "127.0.0.1"
    assert settings.api_port == 8000
    assert settings.readiness_timeout_seconds == 2.0
    assert settings.database_url == API_REQUIRED["FORGE_DATABASE_URL"]
    assert settings.redis_url == API_REQUIRED["FORGE_REDIS_URL"]


def test_worker_needs_only_the_database() -> None:
    settings = WorkerSettings.from_env({**DATABASE, "FORGE_WORKER_ID": "w1"})

    assert settings.worker_id == "w1"
    assert settings.lease_seconds == 30.0
    assert settings.poll_interval_seconds == 1.0
    assert settings.demo_tool_delay_seconds == 0.0


def test_log_level_is_case_insensitive() -> None:
    assert ApiSettings.from_env({**API_REQUIRED, "FORGE_LOG_LEVEL": "debug"}).log_level == "DEBUG"


def test_every_problem_is_reported_together() -> None:
    with pytest.raises(ConfigError) as excinfo:
        ApiSettings.from_env(
            {"FORGE_LOG_LEVEL": "loud", "FORGE_API_PORT": "eighty", "FORGE_OTEL_EXPORTER": "x"}
        )

    message = str(excinfo.value)
    for name in (
        "FORGE_LOG_LEVEL",
        "FORGE_API_PORT",
        "FORGE_OTEL_EXPORTER",
        "FORGE_DATABASE_URL",
        "FORGE_REDIS_URL",
    ):
        assert name in message


@pytest.mark.parametrize("port", ["0", "65536", "-1"])
def test_out_of_range_port_is_rejected(port: str) -> None:
    with pytest.raises(ConfigError, match="FORGE_API_PORT"):
        ApiSettings.from_env({**API_REQUIRED, "FORGE_API_PORT": port})


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("FORGE_WORKER_LEASE_SECONDS", "0"),
        ("FORGE_WORKER_POLL_SECONDS", "-1"),
        ("FORGE_DEMO_TOOL_DELAY_SECONDS", "-1"),
        ("FORGE_WORKER_LEASE_SECONDS", "soon"),
    ],
)
def test_invalid_worker_numbers_are_rejected(name: str, value: str) -> None:
    with pytest.raises(ConfigError, match=name):
        WorkerSettings.from_env({**DATABASE, name: value})


def test_connection_urls_never_appear_in_repr() -> None:
    assert "hunter2" not in repr(ApiSettings.from_env(API_REQUIRED))
    assert "hunter2" not in repr(WorkerSettings.from_env(DATABASE))
