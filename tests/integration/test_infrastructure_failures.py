"""Partial infrastructure failure: the database disappears, connections die mid-step,
and every long-running loop waits it out instead of taking the worker down (ADR 0011)."""

import asyncio
from collections.abc import Awaitable, Callable
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from forge.domain.execution import ExecutionStatus
from forge.domain.mission import MissionStatus
from forge.domain.repositories import StoreUnavailableError
from forge.domain.tool import Idempotency
from forge.evaluation import worker as evaluation_worker
from forge.infrastructure import postgres
from forge.infrastructure.clock import SystemClock
from forge.infrastructure.store import PostgresUnitOfWork
from forge.learning import worker as learning_worker
from forge.learning.generation import DeterministicSkillGenerator
from forge.runtime.agent_loop import claim_next
from forge.runtime.worker import Worker
from tests.integration.conftest import UowFactory
from tests.runtime_doubles import (
    Outage,
    RecordingTool,
    ScriptedModel,
    answer,
    runtime,
    submit,
    tool_call,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def eventually(check: Callable[[], Awaitable[bool]], attempts: int = 400) -> None:
    for _ in range(attempts):
        if await check():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not reached")


def refused_at_least(outage: Outage, n: int) -> Callable[[], Awaitable[bool]]:
    async def check() -> bool:
        return outage.refused >= n

    return check


def completed(uow: UowFactory, mission_id: UUID) -> Callable[[], Awaitable[bool]]:
    async def check() -> bool:
        return await mission_status(uow, mission_id) is MissionStatus.COMPLETED

    return check


async def mission_status(uow: UowFactory, mission_id: UUID) -> MissionStatus:
    async with uow() as tx:
        return (await tx.missions.get(mission_id)).status


async def test_an_unreachable_database_is_reported_as_unavailable_not_as_a_bug() -> None:
    engine = postgres.create_engine("postgresql+psycopg://forge:forge@127.0.0.1:1/forge")
    try:
        with pytest.raises(StoreUnavailableError) as excinfo:
            async with PostgresUnitOfWork(engine) as tx:
                await tx.agents.list_current()
        assert excinfo.value.operation == "connect"
    finally:
        await engine.dispose()


async def test_the_claim_loop_survives_an_outage_and_finishes_the_mission(
    uow: UowFactory,
) -> None:
    outage = Outage(uow)
    mission_id = await submit(uow)
    outage.down = True
    model, tool = ScriptedModel(tool_call(), answer()), RecordingTool()
    rt = runtime(outage, model, tool)  # type: ignore[arg-type]
    stop = asyncio.Event()
    worker = asyncio.create_task(Worker(rt, poll_interval_seconds=0.01).run_until(stop))

    await eventually(refused_at_least(outage, 3))  # backing off, not crashed
    assert not worker.done()
    outage.down = False
    try:
        await eventually(completed(uow, mission_id))
    finally:
        stop.set()
        await worker
    assert len(tool.invocations) == 1


async def test_a_short_outage_during_a_step_does_not_lose_the_lease(uow: UowFactory) -> None:
    outage = Outage(uow)
    release = asyncio.Event()

    async def blip() -> None:
        outage.down = True
        await asyncio.sleep(0.3)  # shorter than the lease, longer than a heartbeat interval
        outage.down = False
        release.set()

    model, tool = ScriptedModel(tool_call(), answer()), RecordingTool(on_invoke=blip)
    await submit(uow)
    rt = runtime(outage, model, tool, lease_seconds=1.5)  # type: ignore[arg-type]
    claim = await claim_next(rt)
    assert claim is not None

    final = await Worker(rt, poll_interval_seconds=0.01).run_claim(claim)

    assert release.is_set()
    assert final is not None
    assert final.status is ExecutionStatus.COMPLETED
    assert final.attempt == 1  # never taken over


async def test_a_long_outage_abandons_the_step_and_another_worker_recovers(
    uow: UowFactory,
) -> None:
    outage = Outage(uow)

    async def outage_outlasting_the_lease() -> None:
        outage.down = True
        await asyncio.sleep(1.5)

    model = ScriptedModel(tool_call(), answer())
    tool = RecordingTool(idempotency=Idempotency.IDEMPOTENT, on_invoke=outage_outlasting_the_lease)
    mission_id = await submit(uow)
    rt = runtime(outage, model, tool, lease_seconds=0.6)  # type: ignore[arg-type]
    claim = await claim_next(rt)
    assert claim is not None

    abandoned = await Worker(rt, poll_interval_seconds=0.01).run_claim(claim)
    outage.down = False
    await asyncio.sleep(0.7)  # the lease the first worker could not renew runs out
    retry = runtime(uow, model, RecordingTool(idempotency=Idempotency.IDEMPOTENT), worker_id="w2")
    claim2 = await claim_next(retry)
    assert claim2 is not None
    final = await Worker(retry, poll_interval_seconds=0.01).run_claim(claim2)

    assert abandoned is None  # stopped as lost: it could not prove it still held the lease
    assert final is not None
    assert final.status is ExecutionStatus.COMPLETED
    assert final.attempt == 2
    assert await mission_status(uow, mission_id) is MissionStatus.COMPLETED


async def test_connections_killed_mid_step_are_recovered(
    uow: UowFactory, engine: AsyncEngine
) -> None:
    async def kill_worker_connections() -> None:
        async with engine.connect() as admin:
            await admin.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND pid <> pg_backend_pid()"
                )
            )

    model = ScriptedModel(tool_call(), answer())
    tool = RecordingTool(on_invoke=kill_worker_connections)
    mission_id = await submit(uow)
    stop = asyncio.Event()
    worker = asyncio.create_task(
        Worker(runtime(uow, model, tool, lease_seconds=0.5), poll_interval_seconds=0.01).run_until(
            stop
        )
    )
    try:
        await eventually(completed(uow, mission_id))
    finally:
        stop.set()
        await worker
    assert tool.invocations  # the READ_ONLY read ran, and the worker never died


async def test_evaluation_and_learning_loops_back_off_instead_of_dying(uow: UowFactory) -> None:
    outage = Outage(uow)
    outage.down = True
    stop = asyncio.Event()
    clock = SystemClock()
    loops = [
        asyncio.create_task(
            evaluation_worker.run_until(outage, clock, stop, poll_interval_seconds=0.01)  # type: ignore[arg-type]
        ),
        asyncio.create_task(
            learning_worker.run_until(
                outage,  # type: ignore[arg-type]
                clock,
                stop,
                poll_interval_seconds=0.01,
                generator=DeterministicSkillGenerator(),
                auto_detect=True,
            )
        ),
    ]
    await eventually(refused_at_least(outage, 4))
    assert not any(loop.done() for loop in loops)
    outage.down = False
    stop.set()
    await asyncio.gather(*loops)
