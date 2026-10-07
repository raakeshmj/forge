"""Run a worker: `python -m forge.runtime`. This is the worker's composition root."""

import asyncio
import signal

from forge.config import WorkerSettings
from forge.inference.demo import DemoInvestigationModel
from forge.inference.pricing import DEMO_MODEL, PRICES
from forge.infrastructure import postgres
from forge.infrastructure.clock import SystemClock
from forge.infrastructure.store import PostgresUnitOfWork
from forge.observability.logs import configure_logging
from forge.observability.tracing import configure_tracing
from forge.runtime.agent_loop import Runtime
from forge.runtime.worker import Worker
from forge.tools.demo import MetricsReadTool


async def serve(settings: WorkerSettings) -> None:
    engine = postgres.create_engine(settings.database_url)
    metrics = MetricsReadTool(simulated_latency_seconds=settings.demo_tool_delay_seconds)
    runtime = Runtime(
        uow_factory=lambda: PostgresUnitOfWork(engine),
        clock=SystemClock(),
        models={DEMO_MODEL.provider: DemoInvestigationModel()},
        prices=PRICES,
        tools={metrics.definition.name: metrics},
        worker_id=settings.worker_id,
        lease_seconds=settings.lease_seconds,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    worker = asyncio.create_task(
        Worker(runtime, poll_interval_seconds=settings.poll_interval_seconds).run_until(stop)
    )
    await stop.wait()
    worker.cancel()  # abandons the in-flight step; its lease is released for another worker
    try:
        await worker
    except asyncio.CancelledError:
        pass
    finally:
        await engine.dispose()


def main() -> None:
    settings = WorkerSettings.from_env()
    configure_logging(settings.log_level)
    provider = configure_tracing(settings.otel_exporter, service_name="forge-worker")
    try:
        asyncio.run(serve(settings))
    finally:
        if provider is not None:
            provider.shutdown()


if __name__ == "__main__":
    main()
