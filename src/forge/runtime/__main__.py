"""Run a worker: `python -m forge.runtime`. This is the worker's composition root."""

import asyncio
import os
import signal
from collections.abc import Coroutine, Mapping
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from forge.capabilities import engine as capability_engine
from forge.config import WorkerSettings
from forge.evaluation import benchmarks
from forge.evaluation import worker as evaluation_worker
from forge.inference.anthropic_client import AnthropicModelClient
from forge.inference.client import ModelClient
from forge.inference.demo import DemoInvestigationModel
from forge.inference.openai_client import OpenAIModelClient
from forge.inference.profiles import DEMO_MODEL, PROFILES
from forge.infrastructure import postgres
from forge.infrastructure.clock import SystemClock
from forge.infrastructure.store import PostgresUnitOfWork
from forge.learning import worker as learning_worker
from forge.learning.generation import DeterministicSkillGenerator
from forge.observability.logs import configure_logging
from forge.observability.tracing import configure_tracing
from forge.runtime.agent_loop import Runtime
from forge.runtime.worker import Worker
from forge.security.secrets import EnvSecretResolver
from forge.tools import config as tool_config
from forge.tools import mcp as mcp_adapter
from forge.tools import registry
from forge.tools.base import Tool
from forge.tools.demo import MetricsReadTool
from forge.tools.http_api import HttpApiTool
from forge.tools.mcp import McpConnection
from forge.tools.registry import Discovered
from forge.tools.search import ToolSearchTool


def configured_models(env: Mapping[str, str]) -> dict[str, ModelClient]:
    """The demo provider always; real providers only when their credentials are present.

    Unconfigured providers are reported by the router as ineligible, never called.
    """
    models: dict[str, ModelClient] = {DEMO_MODEL.provider: DemoInvestigationModel()}
    if env.get("ANTHROPIC_API_KEY") or env.get("ANTHROPIC_AUTH_TOKEN"):
        models["anthropic"] = AnthropicModelClient.from_environment()
    if env.get("OPENAI_API_KEY"):
        models["openai"] = OpenAIModelClient.from_environment()
    return models


async def discover_tools(settings: WorkerSettings, stack: AsyncExitStack) -> list[Discovered]:
    """Native tools, plus MCP servers and remote API tools from the operator's configuration."""
    native: list[Tool] = [
        MetricsReadTool(simulated_latency_seconds=settings.demo_tool_delay_seconds),
        ToolSearchTool(),
    ]
    discovered = [Discovered(tool, source_ref=f"native:{tool.definition.name}") for tool in native]
    if settings.tool_config_path is None:
        return discovered
    config = tool_config.load(Path(settings.tool_config_path))
    for server in config.mcp_servers:
        connection = await McpConnection.open(server, stack)
        discovered.extend(await mcp_adapter.discover(connection, server))
    secrets = EnvSecretResolver()
    for http_tool in config.http_tools:
        discovered.append(
            Discovered(HttpApiTool(http_tool, secrets), source_ref=f"remote:{http_tool.url}")
        )
    return discovered


async def serve(settings: WorkerSettings) -> None:
    engine = postgres.create_engine(settings.database_url)
    clock = SystemClock()
    stack = AsyncExitStack()
    uow_factory = lambda: PostgresUnitOfWork(engine)  # noqa: E731
    registered = await registry.sync(uow_factory, clock, await discover_tools(settings, stack))
    runtime = Runtime(
        uow_factory=uow_factory,
        clock=clock,
        models=configured_models(os.environ),
        profiles=PROFILES,
        tools=registered.active,
        worker_id=settings.worker_id,
        lease_seconds=settings.lease_seconds,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    await benchmarks.register_builtin(uow_factory, clock)
    await capability_engine.register_builtin(uow_factory, clock)
    worker = asyncio.create_task(
        _run_all(
            Worker(
                runtime,
                poll_interval_seconds=settings.poll_interval_seconds,
                concurrency=settings.concurrency,
            ).run_until(stop),
            evaluation_worker.run_until(
                uow_factory, clock, stop, poll_interval_seconds=settings.poll_interval_seconds
            ),
            learning_worker.run_until(
                uow_factory,
                clock,
                stop,
                poll_interval_seconds=max(settings.poll_interval_seconds, 2.0),
                generator=DeterministicSkillGenerator(),
                auto_detect=settings.learning_auto,
            ),
        )
    )
    await stop.wait()
    worker.cancel()  # abandons the in-flight step; its lease is released for another worker
    try:
        await worker
    except asyncio.CancelledError:
        pass
    finally:
        await stack.aclose()
        await engine.dispose()


async def _run_all(*loops: Coroutine[Any, Any, None]) -> None:
    async with asyncio.TaskGroup() as group:
        for loop in loops:
            group.create_task(loop)


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
