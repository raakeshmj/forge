"""Task: a unit of executable work in a mission's task graph (docs/DOMAIN_MODEL.md §3-4, ADR 0006).

Task-level budget, deadline, and risk are not modelled separately: the mission's
apply to every task, and budgets are shared through reservations.
"""

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from forge.domain.common import DomainError, require_transition


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL


_TERMINAL = frozenset({TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED})


class TaskKind(StrEnum):
    WORK = "WORK"
    PLANNING = "PLANNING"  # a planner agent's task; its output is the mission's plan


_S = TaskStatus
TASK_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    _S.PENDING: frozenset({_S.READY, _S.FAILED, _S.CANCELLED}),  # FAILED: no eligible agent
    _S.READY: frozenset({_S.RUNNING, _S.CANCELLED}),
    # RUNNING -> READY is a retry: the failed attempt's execution is kept, a new one runs.
    _S.RUNNING: frozenset({_S.READY, _S.PAUSED, _S.BLOCKED, _S.FAILED, _S.COMPLETED, _S.CANCELLED}),
    _S.PAUSED: frozenset({_S.RUNNING, _S.CANCELLED}),
    _S.BLOCKED: frozenset({_S.RUNNING, _S.CANCELLED}),
}


@dataclass(frozen=True, slots=True)
class Task:
    id: UUID
    version: int
    mission_id: UUID
    objective: str
    agent_id: UUID | None  # None until routed, when the task becomes READY
    allowed_tools: tuple[str, ...]
    status: TaskStatus
    created_at: datetime
    updated_at: datetime
    key: str = "main"
    kind: TaskKind = TaskKind.WORK
    dependencies: tuple[UUID, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    max_attempts: int = 1
    attempts: int = 0

    def __post_init__(self) -> None:
        if not self.objective.strip():
            raise DomainError("task objective must not be empty")
        if not 1 <= self.max_attempts <= 5:
            raise DomainError("max_attempts must be between 1 and 5")
        if (
            self.status is not TaskStatus.PENDING
            and self.agent_id is None
            and not (self.status.is_terminal)
        ):
            raise DomainError("a task must be routed to an agent before it can run")
        if list(self.allowed_tools) != sorted(set(self.allowed_tools)):
            raise DomainError("allowed_tools must be sorted and unique")

    def transition(self, target: TaskStatus, now: datetime) -> "Task":
        require_transition(TASK_TRANSITIONS, "task", self.id, self.status, target)
        return replace(self, status=target, updated_at=now)

    @property
    def can_retry(self) -> bool:
        return self.attempts < self.max_attempts
