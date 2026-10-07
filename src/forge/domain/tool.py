"""Tool definitions and results (docs/DOMAIN_MODEL.md §11)."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from forge.domain.common import DomainError, JSONValue
from forge.domain.retry import RetryPolicy
from forge.domain.risk import RiskLevel


class Idempotency(StrEnum):
    """What re-executing a call with the same idempotency key does (ADR 0002)."""

    READ_ONLY = "READ_ONLY"  # no side effects
    IDEMPOTENT = "IDEMPOTENT"  # side effects deduplicated by the idempotency key
    NON_IDEMPOTENT = "NON_IDEMPOTENT"  # may repeat side effects: never re-executed

    @property
    def safe_to_repeat(self) -> bool:
        return self is not Idempotency.NON_IDEMPOTENT


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

    def __post_init__(self) -> None:
        if not self.name:
            raise DomainError("tool name is required")
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
