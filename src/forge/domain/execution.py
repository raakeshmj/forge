"""Execution: one run of a task by a specific agent version (docs/DOMAIN_MODEL.md §13).

An execution is created when a worker first claims a task and is RUNNING until
terminal. A worker crash leaves it RUNNING with an expiring lease; recovery is a
new attempt of the same execution (ADR 0002).
"""

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from forge.domain.agent import ModelRef
from forge.domain.common import require_transition
from forge.domain.cost import Usage


class ExecutionStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


_S = ExecutionStatus
EXECUTION_TRANSITIONS: dict[ExecutionStatus, frozenset[ExecutionStatus]] = {
    _S.RUNNING: frozenset({_S.COMPLETED, _S.FAILED, _S.CANCELLED}),
}


class FailureKind(StrEnum):
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
    MAX_STEPS_EXCEEDED = "MAX_STEPS_EXCEEDED"
    MODEL_FAILED = "MODEL_FAILED"
    TOOL_OUTCOME_UNKNOWN = "TOOL_OUTCOME_UNKNOWN"
    RECOVERY_EXHAUSTED = "RECOVERY_EXHAUSTED"
    NO_ELIGIBLE_MODEL = "NO_ELIGIBLE_MODEL"
    CONTEXT_EXCEEDED = "CONTEXT_EXCEEDED"


@dataclass(frozen=True, slots=True)
class Execution:
    id: UUID
    mission_id: UUID
    task_id: UUID
    agent_id: UUID
    agent_version_id: UUID
    model: ModelRef
    status: ExecutionStatus
    attempt: int
    usage: Usage
    cost_micro_usd: int
    output: str | None
    failure_kind: FailureKind | None
    failure_detail: str | None
    started_at: datetime
    completed_at: datetime | None

    def record_spend(self, usage: Usage, cost_micro_usd: int) -> "Execution":
        return replace(
            self, usage=self.usage + usage, cost_micro_usd=self.cost_micro_usd + cost_micro_usd
        )

    def complete(self, output: str, now: datetime) -> "Execution":
        return self._finish(_S.COMPLETED, now, output=output)

    def fail(self, kind: FailureKind, detail: str, now: datetime) -> "Execution":
        return self._finish(_S.FAILED, now, failure_kind=kind, failure_detail=detail)

    def cancel(self, now: datetime) -> "Execution":
        return self._finish(_S.CANCELLED, now)

    def _finish(
        self,
        target: ExecutionStatus,
        now: datetime,
        *,
        output: str | None = None,
        failure_kind: FailureKind | None = None,
        failure_detail: str | None = None,
    ) -> "Execution":
        require_transition(EXECUTION_TRANSITIONS, "execution", self.id, self.status, target)
        return replace(
            self,
            status=target,
            output=output,
            failure_kind=failure_kind,
            failure_detail=failure_detail,
            completed_at=now,
        )
