"""Worker: claims executions, keeps their leases alive, and survives individual failures."""

import asyncio
import contextlib
import logging

from forge.domain.execution import Execution
from forge.domain.repositories import LeaseLostError
from forge.runtime.agent_loop import Claim, ExecutionRunner, Runtime, claim_next

_log = logging.getLogger(__name__)


class Worker:
    def __init__(self, runtime: Runtime, *, poll_interval_seconds: float) -> None:
        self._rt = runtime
        self._poll_interval = poll_interval_seconds

    async def run_until(self, stop: asyncio.Event) -> None:
        """Claim and run executions until `stop` is set. Cancelling releases the current lease."""
        while not stop.is_set():
            claim = await claim_next(self._rt)
            if claim is None:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=self._poll_interval)
                continue
            await self.run_claim(claim)

    async def run_claim(self, claim: Claim) -> Execution | None:
        """Run one claim to completion. Returns None if the lease was lost or the run crashed."""
        runner = ExecutionRunner(self._rt, claim)
        run = asyncio.create_task(runner.run())
        lease_lost = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat(claim, run, runner, lease_lost))
        context = {"execution_id": str(claim.execution.id), "attempt": claim.execution.attempt}
        try:
            return await run
        except LeaseLostError:
            _log.warning("lease lost; abandoning execution", extra=context)
            return None
        except asyncio.CancelledError:
            if lease_lost.is_set() and not _being_cancelled():
                _log.warning("lease lost during a step; abandoning execution", extra=context)
                return None
            raise
        except Exception:  # noqa: BLE001 - recovery: release the lease so the next attempt runs
            _log.exception("execution attempt crashed", extra=context)
            return None
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
            if not runner.finished and not lease_lost.is_set():
                await self._release(claim)

    async def _heartbeat(
        self,
        claim: Claim,
        run: asyncio.Task[Execution],
        runner: ExecutionRunner,
        lost: asyncio.Event,
    ) -> None:
        interval = self._rt.lease_seconds / 3
        while True:
            await asyncio.sleep(interval)
            async with self._rt.uow_factory() as uow:
                held = await uow.executions.renew(
                    claim.execution.id, fence=claim.fence, lease_seconds=self._rt.lease_seconds
                )
            if not held and not runner.finished:
                lost.set()
                run.cancel()  # stop work whose results could no longer be committed
                return

    async def _release(self, claim: Claim) -> None:
        """Let another worker resume immediately instead of waiting for the lease to expire."""
        async with self._rt.uow_factory() as uow:
            await uow.executions.release(claim.execution.id, fence=claim.fence)


def _being_cancelled() -> bool:
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0
