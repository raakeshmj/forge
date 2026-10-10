"""Worker: claims executions, keeps their leases alive, and survives individual failures."""

import asyncio
import contextlib
import logging
import time

from forge.domain.execution import Execution
from forge.domain.repositories import LeaseLostError, StoreUnavailableError, outage_delay
from forge.runtime.agent_loop import Claim, ExecutionRunner, Runtime, claim_next

_log = logging.getLogger(__name__)


class Worker:
    def __init__(
        self, runtime: Runtime, *, poll_interval_seconds: float, concurrency: int = 1
    ) -> None:
        self._rt = runtime
        self._poll_interval = poll_interval_seconds
        self._concurrency = concurrency

    async def run_until(self, stop: asyncio.Event) -> None:
        """Run `concurrency` claim loops until `stop` is set. Cancelling releases held leases."""
        async with asyncio.TaskGroup() as group:
            for _ in range(self._concurrency):
                group.create_task(self._claim_loop(stop))

    async def _claim_loop(self, stop: asyncio.Event) -> None:
        outages = 0
        while not stop.is_set():
            try:
                claim = await claim_next(self._rt)
                outages = 0
            except StoreUnavailableError as exc:  # recovery: wait out the outage (ADR 0011)
                outages += 1
                _log.warning(
                    "database unavailable; claiming backs off",
                    extra={"operation": exc.operation, "attempt": outages, "retryable": True},
                )
                await _sleep_unless_stopped(stop, outage_delay(outages))
                continue
            if claim is None:
                await _sleep_unless_stopped(stop, self._poll_interval)
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
        renewed_at = time.monotonic()
        while True:
            await asyncio.sleep(interval)
            try:
                async with self._rt.uow_factory() as uow:
                    held = await uow.executions.renew(
                        claim.execution.id, fence=claim.fence, lease_seconds=self._rt.lease_seconds
                    )
                renewed_at = time.monotonic()
            except StoreUnavailableError:
                # Unknown: keep trying while the last renewal still covers us; past that,
                # another worker may legitimately take over, so stop (ADR 0011).
                held = time.monotonic() - renewed_at < self._rt.lease_seconds - interval
                _log.warning(
                    "lease renewal failed: database unavailable",
                    extra={"execution_id": str(claim.execution.id), "retryable": True},
                )
            if not held and not runner.finished:
                lost.set()
                run.cancel()  # stop work whose results could no longer be committed
                return

    async def _release(self, claim: Claim) -> None:
        """Let another worker resume immediately instead of waiting for the lease to expire."""
        try:
            async with self._rt.uow_factory() as uow:
                await uow.executions.release(claim.execution.id, fence=claim.fence)
        except StoreUnavailableError:  # recovery: the lease simply expires instead
            _log.warning(
                "lease release skipped: database unavailable",
                extra={"execution_id": str(claim.execution.id), "retryable": True},
            )


async def _sleep_unless_stopped(stop: asyncio.Event, seconds: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=seconds)


def _being_cancelled() -> bool:
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0
