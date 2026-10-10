"""The task workflow: deterministic, and free of FORGE runtime imports (ADR 0011).

One workflow per task attempt. It drives the attempt through the `forge.drive`
activity and, while the execution is parked for a human, sleeps on a durable timer
until the approval's expiry, the mission's deadline, a `wake` signal, or the poll
interval, whichever comes first. Its history holds only ids, statuses, and wait
durations: never prompts, outputs, arguments, or credentials.
"""

import contextlib
from dataclasses import dataclass, replace
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

WORKFLOW = "forge.task"
DRIVE = "forge.drive"
WAKE = "wake"
PARKED = "WAITING_APPROVAL"


def workflow_id(task_id: str, attempt: int) -> str:
    """Workflow-id uniqueness is what makes dispatch idempotent."""
    return f"forge-task-{task_id}-{attempt}"


@dataclass(frozen=True)
class TaskRun:
    task_id: str
    attempt: int
    poll_seconds: float = 60.0  # bounds the cost of a lost wake signal
    heartbeat_seconds: float = 30.0  # a silent activity is presumed dead after this
    waits_before_continue: int = 500  # keeps history bounded on long waits
    waits: int = 0


@dataclass(frozen=True)
class DriveRequest:
    task_id: str
    attempt: int


@dataclass(frozen=True)
class DriveResult:
    status: str
    execution_id: str | None
    wake_in_seconds: float | None  # parked: until the earliest expiry or deadline


DRIVE_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    # Recovery is bounded by the execution's own attempt limit (ADR 0002), not here.
    non_retryable_error_types=["NotFoundError"],
)


@workflow.defn(name=WORKFLOW)
class TaskWorkflow:
    def __init__(self) -> None:
        self._woken = False

    @workflow.signal(name=WAKE)
    def wake(self) -> None:
        """Something this task waits on changed in Postgres; look now rather than later."""
        self._woken = True

    @workflow.run
    async def run(self, run: TaskRun) -> str:
        waits = run.waits
        while True:
            self._woken = False
            result: DriveResult = await workflow.execute_activity(
                DRIVE,
                DriveRequest(run.task_id, run.attempt),
                result_type=DriveResult,
                start_to_close_timeout=timedelta(days=7),
                heartbeat_timeout=timedelta(seconds=run.heartbeat_seconds),
                retry_policy=DRIVE_RETRY,
            )
            if result.status != PARKED:
                return result.status
            waits += 1
            if waits >= run.waits_before_continue or workflow.info().is_continue_as_new_suggested():
                workflow.continue_as_new(replace(run, waits=0))
            wait = run.poll_seconds
            if result.wake_in_seconds is not None:
                wait = max(1.0, min(wait, result.wake_in_seconds))
            if not self._woken:
                with contextlib.suppress(TimeoutError):  # the durable timer fired
                    await workflow.wait_condition(
                        lambda: self._woken, timeout=timedelta(seconds=wait)
                    )
