"""The `forge.drive` activity: one task attempt driven by the shared runtime (ADR 0011)."""

import asyncio
import contextlib
from uuid import UUID

from temporalio import activity

from forge.infrastructure.temporal.workflows import DRIVE, DriveRequest, DriveResult
from forge.runtime.agent_loop import Runtime
from forge.runtime.drive import drive


class DriveActivities:
    def __init__(self, runtime: Runtime, *, heartbeat_seconds: float) -> None:
        self._rt = runtime
        self._beat_every = heartbeat_seconds / 3

    @activity.defn(name=DRIVE)
    async def drive_task(self, request: DriveRequest) -> DriveResult:
        """Heartbeats to Temporal while the Postgres lease is renewed by the runtime.

        A dead worker stops heartbeating; Temporal retries the activity elsewhere, and the
        retry takes over the execution once its lease has expired.
        """
        beat = asyncio.create_task(self._heartbeat())
        try:
            outcome = await drive(self._rt, UUID(request.task_id), request.attempt)
        finally:
            beat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await beat
        wake_in = None
        if outcome.wake_at is not None:
            wake_in = (outcome.wake_at - self._rt.clock.now()).total_seconds()
        execution = None if outcome.execution_id is None else str(outcome.execution_id)
        return DriveResult(outcome.status, execution, wake_in)

    async def _heartbeat(self) -> None:
        while True:
            activity.heartbeat()
            await asyncio.sleep(self._beat_every)
