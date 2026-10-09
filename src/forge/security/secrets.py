"""Credential resolution at execution time (SECURITY §6, ADR 0005, ADR 0010).

Tools hold references; values are resolved only inside tool execution and never
enter definitions, arguments, events, telemetry, or model context. A reference
resolves only for the agents it is granted to: no grant, no credential.
"""

import os
from collections.abc import Mapping
from typing import Protocol


class SecretUnavailableError(LookupError):
    def __init__(self, reference: str) -> None:
        super().__init__(f"secret {reference!r} is not available")  # the name only, never a value
        self.reference = reference


class SecretNotGrantedError(PermissionError):
    def __init__(self, reference: str, agent: str) -> None:
        super().__init__(f"secret {reference!r} is not granted to agent {agent!r}")
        self.reference = reference
        self.agent = agent


class SecretResolver(Protocol):
    def resolve(self, reference: str, *, agent: str) -> str: ...


class EnvSecretResolver:
    """Resolves `name` from the FORGE_SECRET_<NAME> environment variable.

    `grants` maps each reference to the agent names allowed to use it.
    """

    def __init__(
        self,
        env: Mapping[str, str] | None = None,
        *,
        grants: Mapping[str, frozenset[str]] | None = None,
    ) -> None:
        self._env = os.environ if env is None else env
        self._grants = dict(grants or {})

    def resolve(self, reference: str, *, agent: str) -> str:
        if agent not in self._grants.get(reference, frozenset()):
            raise SecretNotGrantedError(reference, agent)
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
