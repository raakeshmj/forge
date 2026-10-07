"""Task: a unit of executable work within a mission (docs/DOMAIN_MODEL.md §3-4).

Phase 1 carries only the fields a single-task mission uses; dependencies,
task-level budget, deadline, and risk arrive with multi-task orchestration.
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

_S = TaskStatus
TASK_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    _S.PENDING: frozenset({_S.READY, _S.CANCELLED}),
    _S.READY: frozenset({_S.RUNNING, _S.CANCELLED}),
    _S.RUNNING: frozenset({_S.PAUSED, _S.BLOCKED, _S.FAILED, _S.COMPLETED, _S.CANCELLED}),
    _S.PAUSED: frozenset({_S.RUNNING, _S.CANCELLED}),
    _S.BLOCKED: frozenset({_S.RUNNING, _S.CANCELLED}),
}


@dataclass(frozen=True, slots=True)
class Task:
    id: UUID
    version: int
    mission_id: UUID
    objective: str
    agent_id: UUID
    allowed_tools: tuple[str, ...]
    status: TaskStatus
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not self.objective.strip():
            raise DomainError("task objective must not be empty")
        if list(self.allowed_tools) != sorted(set(self.allowed_tools)):
            raise DomainError("allowed_tools must be sorted and unique")

    def transition(self, target: TaskStatus, now: datetime) -> "Task":
        require_transition(TASK_TRANSITIONS, "task", self.id, self.status, target)
        return replace(self, status=target, updated_at=now)
