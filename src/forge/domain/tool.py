"""Tool definitions and results (docs/DOMAIN_MODEL.md §11)."""

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid5

from forge.domain.common import DomainError, JSONValue
from forge.domain.retry import RetryPolicy
from forge.domain.risk import RiskLevel

_TOOL_NAME = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")


class Idempotency(StrEnum):
    """What re-executing a call with the same idempotency key does (ADR 0002)."""

    READ_ONLY = "READ_ONLY"  # no side effects
    IDEMPOTENT = "IDEMPOTENT"  # side effects deduplicated by the idempotency key
    NON_IDEMPOTENT = "NON_IDEMPOTENT"  # may repeat side effects: never re-executed

    @property
    def safe_to_repeat(self) -> bool:
        return self is not Idempotency.NON_IDEMPOTENT


class ToolSource(StrEnum):
    NATIVE = "native"
    MCP = "mcp"
    REMOTE = "remote"


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    version: str
    description: str
    input_schema: Mapping[str, JSONValue]
    risk: RiskLevel
    idempotency: Idempotency
    timeout_seconds: float
    max_attempts: int = 1
    retry_initial_backoff_seconds: float = 0.5
    retry_max_backoff_seconds: float = 4.0
    source: ToolSource = ToolSource.NATIVE
    deferred: bool = False  # offered to the model only after `tools.search` loads it

    def __post_init__(self) -> None:
        if not _TOOL_NAME.fullmatch(self.name) or "__" in self.name:
            raise DomainError(
                f"tool name {self.name!r} must be 1-64 of [a-z0-9_.-], start alphanumeric, "
                "and not contain '__'"
            )
        if self.timeout_seconds <= 0:
            raise DomainError("tool timeout must be positive")
        if self.max_attempts > 1 and not self.idempotency.safe_to_repeat:
            raise DomainError(f"tool {self.name}: a non-idempotent tool cannot declare retries")

    @property
    def retry(self) -> RetryPolicy:
        return RetryPolicy(
            max_attempts=self.max_attempts,
            initial_backoff_seconds=self.retry_initial_backoff_seconds,
            max_backoff_seconds=self.retry_max_backoff_seconds,
        )


@dataclass(frozen=True, slots=True)
class ToolResult:
    """What a tool returns. `content` goes back to the model as untrusted data."""

    content: str
    is_error: bool = False
    cost_micro_usd: int = 0


class InvocationStatus(StrEnum):
    """INTENDED is committed before execution; still INTENDED after a crash = outcome unknown."""

    INTENDED = "INTENDED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    """Ledger entry for one tool call, keyed by its deterministic idempotency key (ADR 0002)."""

    idempotency_key: str
    execution_id: UUID
    turn: int
    tool_use_id: str
    tool: str
    tool_version: str
    status: InvocationStatus
    invocations: int


def idempotency_key(execution_id: UUID, turn: int, tool_use_id: str) -> str:
    return f"{execution_id}:{turn}:{tool_use_id}"


def definition_sha256(definition: ToolDefinition) -> str:
    """Fingerprint of everything that determines a tool's behaviour and authority (ADR 0005)."""
    canonical = {
        "name": definition.name,
        "version": definition.version,
        "description": definition.description,
        "input_schema": definition.input_schema,
        "risk": definition.risk.value,
        "idempotency": definition.idempotency.value,
        "timeout_seconds": definition.timeout_seconds,
        "max_attempts": definition.max_attempts,
        "source": definition.source.value,
        "deferred": definition.deferred,
    }
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()


class ToolStatus(StrEnum):
    ACTIVE = "ACTIVE"
    QUARANTINED = "QUARANTINED"  # changed since it was pinned; needs an operator to pin again


@dataclass(frozen=True, slots=True)
class ToolVersionRecord:
    """An immutable registered version of a tool."""

    name: str
    version: str
    source: ToolSource
    source_ref: str  # e.g. "mcp:metrics-server/query_latency"
    definition_sha256: str
    registered_at: datetime


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    name: str
    pinned_version: str
    pinned_sha256: str
    status: ToolStatus
    reason: str  # why quarantined; empty when active
    updated_at: datetime


def tool_aggregate_id(name: str) -> UUID:
    """Stable aggregate id for a tool's registry events."""
    return uuid5(_TOOL_NAMESPACE, name)


_TOOL_NAMESPACE = UUID("0b6c2d0e-7a8f-4f6e-9c43-5d2b1a7e9f10")
