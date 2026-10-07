"""Mission: the top-level execution primitive (docs/DOMAIN_MODEL.md §1-2)."""

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from forge.domain.common import DomainError, require_transition
from forge.domain.retry import RetryPolicy
from forge.domain.risk import RiskLevel


class MissionStatus(StrEnum):
    CREATED = "CREATED"
    PLANNING = "PLANNING"
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL


_TERMINAL = frozenset({MissionStatus.COMPLETED, MissionStatus.FAILED, MissionStatus.CANCELLED})

_S = MissionStatus
MISSION_TRANSITIONS: dict[MissionStatus, frozenset[MissionStatus]] = {
    _S.CREATED: frozenset({_S.PLANNING, _S.CANCELLED}),
    _S.PLANNING: frozenset({_S.READY, _S.FAILED, _S.CANCELLED}),
    _S.READY: frozenset({_S.RUNNING, _S.CANCELLED}),
    _S.RUNNING: frozenset({_S.PAUSED, _S.WAITING_APPROVAL, _S.FAILED, _S.COMPLETED, _S.CANCELLED}),
    _S.PAUSED: frozenset({_S.RUNNING, _S.CANCELLED}),
    _S.WAITING_APPROVAL: frozenset({_S.RUNNING, _S.CANCELLED}),
}


@dataclass(frozen=True, slots=True)
class Budget:
    limit_micro_usd: int

    def __post_init__(self) -> None:
        if self.limit_micro_usd <= 0:
            raise DomainError("budget limit must be positive")

    def is_exhausted_by(self, spent_micro_usd: int) -> bool:
        return spent_micro_usd >= self.limit_micro_usd


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    """The highest tool risk this mission permits."""

    max_risk: RiskLevel = RiskLevel.LOW


@dataclass(frozen=True, slots=True)
class ExecutionPolicy:
    max_steps: int = 8
    model_timeout_seconds: float = 60.0
    model_max_attempts: int = 3
    retry_initial_backoff_seconds: float = 0.5
    retry_max_backoff_seconds: float = 8.0

    def __post_init__(self) -> None:
        if self.max_steps < 1:
            raise DomainError("max_steps must be at least 1")
        if self.model_timeout_seconds <= 0:
            raise DomainError("model_timeout_seconds must be positive")

    @property
    def model_retry(self) -> RetryPolicy:
        return RetryPolicy(
            max_attempts=self.model_max_attempts,
            initial_backoff_seconds=self.retry_initial_backoff_seconds,
            max_backoff_seconds=self.retry_max_backoff_seconds,
        )


@dataclass(frozen=True, slots=True)
class Mission:
    id: UUID
    version: int
    objective: str
    constraints: tuple[str, ...]
    success_criteria: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    budget: Budget
    deadline: datetime | None
    risk_policy: RiskPolicy
    execution_policy: ExecutionPolicy
    status: MissionStatus
    correlation_id: str
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not self.objective.strip():
            raise DomainError("mission objective must not be empty")
        if self.deadline is not None and self.deadline.tzinfo is None:
            raise DomainError("mission deadline must be timezone-aware")

    def transition(self, target: MissionStatus, now: datetime) -> "Mission":
        require_transition(MISSION_TRANSITIONS, "mission", self.id, self.status, target)
        return replace(self, status=target, updated_at=now)

    def deadline_passed(self, now: datetime) -> bool:
        return self.deadline is not None and now >= self.deadline
