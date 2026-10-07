"""Process configuration, loaded once from environment variables."""

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field

_LOG_LEVELS = frozenset(logging.getLevelNamesMapping())


class ConfigError(ValueError):
    """Raised when the environment does not describe a valid configuration."""


@dataclass(frozen=True, slots=True)
class Settings:
    log_level: str
    api_host: str
    api_port: int
    readiness_timeout_seconds: float
    # Connection URLs can embed credentials; keep them out of repr() and therefore out of logs.
    database_url: str = field(repr=False)
    redis_url: str = field(repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        """Build settings from `env` (defaults to `os.environ`), reporting every problem at once."""
        source = os.environ if env is None else env
        errors: list[str] = []

        log_level = source.get("FORGE_LOG_LEVEL", "INFO").upper()
        if log_level not in _LOG_LEVELS:
            errors.append(
                f"FORGE_LOG_LEVEL must be one of {sorted(_LOG_LEVELS)}, got {log_level!r}"
            )

        api_port = _parse_int(source, "FORGE_API_PORT", default=8000, errors=errors)
        if not 0 < api_port < 65536:
            errors.append(f"FORGE_API_PORT must be between 1 and 65535, got {api_port}")

        readiness_timeout = _parse_float(
            source, "FORGE_READINESS_TIMEOUT_SECONDS", default=2.0, errors=errors
        )
        if readiness_timeout <= 0:
            errors.append(
                f"FORGE_READINESS_TIMEOUT_SECONDS must be positive, got {readiness_timeout}"
            )

        database_url = _require(source, "FORGE_DATABASE_URL", errors)
        redis_url = _require(source, "FORGE_REDIS_URL", errors)

        if errors:
            raise ConfigError("Invalid configuration: " + "; ".join(errors))

        return cls(
            log_level=log_level,
            api_host=source.get("FORGE_API_HOST", "127.0.0.1"),
            api_port=api_port,
            readiness_timeout_seconds=readiness_timeout,
            database_url=database_url,
            redis_url=redis_url,
        )


def _parse_int(source: Mapping[str, str], name: str, *, default: int, errors: list[str]) -> int:
    """Parse an integer variable; on failure record the error and return `default`."""
    raw = source.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        errors.append(f"{name} must be an integer, got {raw!r}")
        return default


def _parse_float(
    source: Mapping[str, str], name: str, *, default: float, errors: list[str]
) -> float:
    """Parse a float variable; on failure record the error and return `default`."""
    raw = source.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        errors.append(f"{name} must be a number, got {raw!r}")
        return default


def _require(source: Mapping[str, str], name: str, errors: list[str]) -> str:
    """Read a mandatory variable; record an error (without echoing values) if absent or empty."""
    value = source.get(name, "")
    if not value:
        errors.append(f"{name} is required")
    return value
