"""Drive one task attempt to an outcome: the backend-neutral unit of work (ADR 0011).

An external scheduler (the Temporal adapter) calls `drive` for a (task, attempt)
pair. It claims exactly what the Postgres worker would claim for that task (start,
take over an expired lease, or resume a parked execution), runs it under the same
fenced lease, and reports where it stopped. Calling it again for the same pair is
always safe: completed steps are never repeated (ADR 0002).
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from forge.domain.execution import Execution, ExecutionStatus
from forge.domain.task import TaskStatus
from forge.runtime.agent_loop import (
    Claim,
    Runtime,
    expire_approvals,
    resume_parked,
    start_ready,
    take_over_expired,
)
from forge.runtime.worker import Worker

_TERMINAL = frozenset(
    {ExecutionStatus.COMPLETED, ExecutionStatus.FAILED, ExecutionStatus.CANCELLED}
)
SUPERSEDED = "SUPERSEDED"  # the attempt is no longer the task's current one


class LeaseHeldError(RuntimeError):
    """Another worker holds the execution's lease and may be alive. Retryable: try again
    once the lease could have expired."""

    def __init__(self, task_id: UUID, attempt: int) -> None:
        super().__init__(f"task {task_id} attempt {attempt} is held by another worker")
        self.task_id = task_id
        self.attempt = attempt


class RecoveryNeededError(RuntimeError):
    """The run crashed or lost its lease; nothing past its last checkpoint counts. Retryable."""

    def __init__(self, execution_id: UUID) -> None:
        super().__init__(f"execution {execution_id} stopped without an outcome; resume it")
        self.execution_id = execution_id


@dataclass(frozen=True, slots=True)
class Outcome:
    status: str  # an ExecutionStatus value, or SUPERSEDED
    execution_id: UUID | None = None
    wake_at: datetime | None = None  # parked: the earliest approval expiry or deadline

    @property
    def parked(self) -> bool:
        return self.status == ExecutionStatus.WAITING_APPROVAL.value


async def drive(
    runtime: Runtime, task_id: UUID, attempt: int, *, poll_interval_seconds: float = 1.0
) -> Outcome:
    claimed = await claim_task(runtime, task_id, attempt)
    if isinstance(claimed, Outcome):
        return claimed
    final = await Worker(runtime, poll_interval_seconds=poll_interval_seconds).run_claim(claimed)
    if final is None:
        raise RecoveryNeededError(claimed.execution.id)
    if final.status is ExecutionStatus.WAITING_APPROVAL:
        return await parked(runtime, final)
    return Outcome(final.status.value, final.id)


async def claim_task(runtime: Runtime, task_id: UUID, attempt: int) -> Claim | Outcome:
    """Claim this task attempt's execution, or report why there is nothing to run."""
    now = runtime.clock.now()
    await expire_approvals(runtime, now)
    async with runtime.uow_factory() as uow:
        task = await uow.tasks.get(task_id)
        execution = await uow.executions.latest_for_task(task_id)
    if execution is not None and execution.task_attempt == attempt:
        if execution.status in _TERMINAL:
            return Outcome(execution.status.value, execution.id)
        if execution.status is ExecutionStatus.WAITING_APPROVAL:
            return await resume_parked(runtime, now, execution.id) or await parked(
                runtime, execution
            )
        claim = await take_over_expired(runtime, execution.id)
        if claim is None:
            raise LeaseHeldError(task_id, attempt)
        return claim
    if task.attempts + 1 == attempt:
        claim = await start_ready(runtime, now, task_id)
        if claim is not None:
            return claim
        async with runtime.uow_factory() as uow:
            latest = await uow.executions.latest_for_task(task_id)
        if latest is not None and latest.task_attempt == attempt and latest.status in _TERMINAL:
            return Outcome(latest.status.value, latest.id)  # e.g. no eligible model
        if latest is not None and latest.task_attempt == attempt:
            raise LeaseHeldError(task_id, attempt)  # another driver claimed it just now
        if task.status is TaskStatus.READY:
            raise LeaseHeldError(task_id, attempt)  # locked by a concurrent claim
        return Outcome(SUPERSEDED, None)  # cancelled, or not runnable as this attempt
    return Outcome(SUPERSEDED, execution.id if execution else None)


async def parked(runtime: Runtime, execution: Execution) -> Outcome:
    """Where a parked execution stands, and when it should next be looked at."""
    async with runtime.uow_factory() as uow:
        pending = await uow.approvals.pending_for_execution(execution.id)
        mission = await uow.missions.get(execution.mission_id)
    moments = [a.expires_at for a in pending]
    if mission.deadline is not None:
        moments.append(mission.deadline)
    return Outcome(
        ExecutionStatus.WAITING_APPROVAL.value, execution.id, min(moments) if moments else None
    )
