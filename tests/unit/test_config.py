import pytest

from forge.config import ConfigError, Settings

REQUIRED = {
    "FORGE_DATABASE_URL": "postgresql+psycopg://forge:hunter2@db:5432/forge",
    "FORGE_REDIS_URL": "redis://:hunter2@redis:6379/0",
}


def test_defaults_apply_when_optional_variables_are_absent() -> None:
    settings = Settings.from_env(REQUIRED)

    assert settings.log_level == "INFO"
    assert settings.api_host == "127.0.0.1"
    assert settings.api_port == 8000
    assert settings.readiness_timeout_seconds == 2.0
    assert settings.database_url == REQUIRED["FORGE_DATABASE_URL"]
    assert settings.redis_url == REQUIRED["FORGE_REDIS_URL"]


def test_log_level_is_case_insensitive() -> None:
    assert Settings.from_env({**REQUIRED, "FORGE_LOG_LEVEL": "debug"}).log_level == "DEBUG"


def test_every_problem_is_reported_together() -> None:
    with pytest.raises(ConfigError) as excinfo:
        Settings.from_env({"FORGE_LOG_LEVEL": "loud", "FORGE_API_PORT": "eighty"})

    message = str(excinfo.value)
    for name in ("FORGE_LOG_LEVEL", "FORGE_API_PORT", "FORGE_DATABASE_URL", "FORGE_REDIS_URL"):
        assert name in message


@pytest.mark.parametrize("port", ["0", "65536", "-1"])
def test_out_of_range_port_is_rejected(port: str) -> None:
    with pytest.raises(ConfigError, match="FORGE_API_PORT"):
        Settings.from_env({**REQUIRED, "FORGE_API_PORT": port})


@pytest.mark.parametrize("timeout", ["0", "-1", "soon"])
def test_invalid_readiness_timeout_is_rejected(timeout: str) -> None:
    with pytest.raises(ConfigError, match="FORGE_READINESS_TIMEOUT_SECONDS"):
        Settings.from_env({**REQUIRED, "FORGE_READINESS_TIMEOUT_SECONDS": timeout})


def test_connection_urls_never_appear_in_repr() -> None:
    assert "hunter2" not in repr(Settings.from_env(REQUIRED))
