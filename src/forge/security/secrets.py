"""Credential resolution at execution time (SECURITY §6, ADR 0005).

Tools hold references; values are resolved only inside tool execution and never
enter definitions, arguments, events, telemetry, or model context.
"""

import os
from collections.abc import Mapping
from typing import Protocol


class SecretUnavailableError(LookupError):
    def __init__(self, reference: str) -> None:
        super().__init__(f"secret {reference!r} is not available")  # the name only, never a value
        self.reference = reference


class SecretResolver(Protocol):
    def resolve(self, reference: str) -> str: ...


class EnvSecretResolver:
    """Resolves `name` from the FORGE_SECRET_<NAME> environment variable."""

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self._env = os.environ if env is None else env

    def resolve(self, reference: str) -> str:
        value = self._env.get(f"FORGE_SECRET_{reference.upper()}")
        if not value:
            raise SecretUnavailableError(reference)
        return value


def redact(text: str, secrets: tuple[str, ...]) -> str:
    """Remove any echoed secret before text can reach a model or a log."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text
