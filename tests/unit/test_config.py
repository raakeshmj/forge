import pytest

from forge.config import ConfigError, Settings


def test_defaults_apply_when_optional_variables_are_absent() -> None:
    settings = Settings.from_env({})

    assert settings.log_level == "INFO"
    assert settings.api_host == "127.0.0.1"
    assert settings.api_port == 8000


def test_log_level_is_case_insensitive() -> None:
    assert Settings.from_env({"FORGE_LOG_LEVEL": "debug"}).log_level == "DEBUG"


def test_every_invalid_value_is_reported_together() -> None:
    with pytest.raises(ConfigError) as excinfo:
        Settings.from_env({"FORGE_LOG_LEVEL": "loud", "FORGE_API_PORT": "eighty"})

    message = str(excinfo.value)
    assert "FORGE_LOG_LEVEL" in message
    assert "FORGE_API_PORT" in message


@pytest.mark.parametrize("port", ["0", "65536", "-1"])
def test_out_of_range_port_is_rejected(port: str) -> None:
    with pytest.raises(ConfigError, match="FORGE_API_PORT"):
        Settings.from_env({"FORGE_API_PORT": port})
