"""Process configuration, loaded once from environment variables.

Each process type reads only what it uses: the API needs Redis for readiness,
the worker does not.
"""

import logging
import os
import socket
from collections.abc import Mapping
from dataclasses import dataclass, field

_LOG_LEVELS = frozenset(logging.getLevelNamesMapping())
OTEL_EXPORTERS = frozenset({"none", "console", "otlp"})


class ConfigError(ValueError):
    """Raised when the environment does not describe a valid configuration."""


@dataclass(frozen=True, slots=True)
class ApiSettings:
    log_level: str
    otel_exporter: str
    api_host: str
    api_port: int
    readiness_timeout_seconds: float
    # Connection URLs can embed credentials; keep them out of repr() and therefore out of logs.
    database_url: str = field(repr=False)
    redis_url: str = field(repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ApiSettings":
        """Build settings from `env` (defaults to `os.environ`), reporting every problem at once."""
        reader = _Reader(os.environ if env is None else env)
        api_port = reader.integer("FORGE_API_PORT", default=8000)
        if not 0 < api_port < 65536:
            reader.errors.append(f"FORGE_API_PORT must be between 1 and 65535, got {api_port}")
        settings = cls(
            log_level=reader.log_level(),
            otel_exporter=reader.otel_exporter(),
            api_host=reader.text("FORGE_API_HOST", default="127.0.0.1"),
            api_port=api_port,
            readiness_timeout_seconds=reader.positive("FORGE_READINESS_TIMEOUT_SECONDS", 2.0),
            database_url=reader.required("FORGE_DATABASE_URL"),
            redis_url=reader.required("FORGE_REDIS_URL"),
        )
        reader.raise_if_invalid()
        return settings


@dataclass(frozen=True, slots=True)
class WorkerSettings:
    log_level: str
    otel_exporter: str
    worker_id: str
    lease_seconds: float
    poll_interval_seconds: float
    demo_tool_delay_seconds: float
    database_url: str = field(repr=False)
    tool_config_path: str | None = None  # MCP servers and remote API tools (ADR 0005)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "WorkerSettings":
        reader = _Reader(os.environ if env is None else env)
        delay = reader.number("FORGE_DEMO_TOOL_DELAY_SECONDS", 0.0)
        if delay < 0:
            reader.errors.append("FORGE_DEMO_TOOL_DELAY_SECONDS must not be negative")
        settings = cls(
            log_level=reader.log_level(),
            otel_exporter=reader.otel_exporter(),
            worker_id=reader.text(
                "FORGE_WORKER_ID", default=f"{socket.gethostname()}-{os.getpid()}"
            ),
            lease_seconds=reader.positive("FORGE_WORKER_LEASE_SECONDS", 30.0),
            poll_interval_seconds=reader.positive("FORGE_WORKER_POLL_SECONDS", 1.0),
            demo_tool_delay_seconds=delay,
            database_url=reader.required("FORGE_DATABASE_URL"),
            tool_config_path=reader.optional("FORGE_TOOL_CONFIG"),
        )
        reader.raise_if_invalid()
        return settings


class _Reader:
    """Parses variables, collecting every error so a misconfiguration is reported in one go."""

    def __init__(self, source: Mapping[str, str]) -> None:
        self._source = source
        self.errors: list[str] = []

    def text(self, name: str, *, default: str) -> str:
        return self._source.get(name, default)

    def optional(self, name: str) -> str | None:
        return self._source.get(name) or None

    def required(self, name: str) -> str:
        value = self._source.get(name, "")
        if not value:
            self.errors.append(f"{name} is required")  # never echo values: they may be secret
        return value

    def log_level(self) -> str:
        level = self._source.get("FORGE_LOG_LEVEL", "INFO").upper()
        if level not in _LOG_LEVELS:
            self.errors.append(
                f"FORGE_LOG_LEVEL must be one of {sorted(_LOG_LEVELS)}, got {level!r}"
            )
        return level

    def otel_exporter(self) -> str:
        exporter = self._source.get("FORGE_OTEL_EXPORTER", "none").lower()
        if exporter not in OTEL_EXPORTERS:
            self.errors.append(
                f"FORGE_OTEL_EXPORTER must be one of {sorted(OTEL_EXPORTERS)}, got {exporter!r}"
            )
        return exporter

    def integer(self, name: str, *, default: int) -> int:
        raw = self._source.get(name)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError:
            self.errors.append(f"{name} must be an integer, got {raw!r}")
            return default

    def number(self, name: str, default: float) -> float:
        raw = self._source.get(name)
        if raw is None:
            return default
        try:
            return float(raw)
        except ValueError:
            self.errors.append(f"{name} must be a number, got {raw!r}")
            return default

    def positive(self, name: str, default: float) -> float:
        value = self.number(name, default)
        if value <= 0:
            self.errors.append(f"{name} must be positive, got {value}")
        return value

    def raise_if_invalid(self) -> None:
        if self.errors:
            raise ConfigError("Invalid configuration: " + "; ".join(self.errors))
