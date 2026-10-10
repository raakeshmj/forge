"""Dispatcher: start a task workflow for every task attempt that needs a driver (ADR 0011).

Every worker runs one. Workflow ids are deterministic, so concurrent dispatchers, and
repeated passes, start each attempt's workflow at most once; a failed or terminated
workflow is restarted if its task still needs driving.
"""

import asyncio
import contextlib
import logging
from dataclasses import dataclass

from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError

from forge.domain.repositories import StoreUnavailableError, UnitOfWorkFactory, outage_delay
from forge.infrastructure.temporal.workflows import WORKFLOW, TaskRun, workflow_id

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DispatchSettings:
    task_queue: str
    poll_interval_seconds: float = 1.0
    batch: int = 100
    approval_poll_seconds: float = 60.0
    heartbeat_seconds: float = 30.0


async def dispatch_once(
    client: Client, uow_factory: UnitOfWorkFactory, settings: DispatchSettings
) -> int:
    """Start workflows for dispatchable task attempts; returns how many were new."""
    async with uow_factory() as uow:
        pairs = await uow.tasks.dispatchable(settings.batch)
    started = 0
    for task_id, attempt in pairs:
        try:
            await client.start_workflow(
                WORKFLOW,
                TaskRun(
                    str(task_id),
                    attempt,
                    poll_seconds=settings.approval_poll_seconds,
                    heartbeat_seconds=settings.heartbeat_seconds,
                ),
                id=workflow_id(str(task_id), attempt),
                task_queue=settings.task_queue,
                id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY,
            )
            started += 1
        except WorkflowAlreadyStartedError:
            continue  # already being driven
    return started


async def run_until(
    client: Client,
    uow_factory: UnitOfWorkFactory,
    settings: DispatchSettings,
    stop: asyncio.Event,
) -> None:
    outages = 0
    while not stop.is_set():
        delay = settings.poll_interval_seconds
        try:
            await dispatch_once(client, uow_factory, settings)
            outages = 0
        except StoreUnavailableError as exc:  # recovery: wait out the outage (ADR 0011)
            outages += 1
            delay = outage_delay(outages)
            _log.warning(
                "database unavailable; dispatch backs off",
                extra={"operation": exc.operation, "attempt": outages, "retryable": True},
            )
        except RPCError as exc:  # recovery: Temporal unreachable; Postgres keeps the work
            outages += 1
            delay = outage_delay(outages)
            _log.warning(
                "temporal unavailable; dispatch backs off",
                extra={"error_type": exc.status.name, "attempt": outages, "retryable": True},
            )
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=delay)
