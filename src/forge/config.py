"""Process configuration, loaded once from environment variables."""

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass

_LOG_LEVELS = frozenset(logging.getLevelNamesMapping())


class ConfigError(ValueError):
    """Raised when the environment does not describe a valid configuration."""


@dataclass(frozen=True, slots=True)
class Settings:
    log_level: str
    api_host: str
    api_port: int

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

        if errors:
            raise ConfigError("Invalid configuration: " + "; ".join(errors))

        return cls(
            log_level=log_level,
            api_host=source.get("FORGE_API_HOST", "127.0.0.1"),
            api_port=api_port,
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
