"""Run a worker on the Temporal backend: task workflows, the drive activity, a dispatcher."""

import asyncio
from datetime import timedelta

from temporalio.client import Client
from temporalio.worker import Worker

from forge.infrastructure.temporal.activities import DriveActivities
from forge.infrastructure.temporal.dispatch import DispatchSettings
from forge.infrastructure.temporal.dispatch import run_until as dispatch_until
from forge.infrastructure.temporal.workflows import TaskWorkflow
from forge.runtime.agent_loop import Runtime


async def connect(address: str, namespace: str) -> Client:
    return await Client.connect(address, namespace=namespace)


async def run_until(
    client: Client,
    runtime: Runtime,
    settings: DispatchSettings,
    stop: asyncio.Event,
    *,
    concurrency: int,
) -> None:
    """Serve workflows and activities, and dispatch, until `stop`. On shutdown in-flight
    activities are cancelled: their leases are released and Temporal retries them."""
    activities = DriveActivities(runtime, heartbeat_seconds=settings.heartbeat_seconds)
    worker = Worker(
        client,
        task_queue=settings.task_queue,
        workflows=[TaskWorkflow],
        activities=[activities.drive_task],
        max_concurrent_activities=concurrency,
        graceful_shutdown_timeout=timedelta(seconds=5),
    )
    async with worker:
        await dispatch_until(client, runtime.uow_factory, settings, stop)
