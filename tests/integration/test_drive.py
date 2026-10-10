"""The per-task driver contract (ADR 0011): what any external scheduler may rely on.

`drive(task, attempt)` is safe to call any number of times, from any number of
workers, at any moment: it claims what the Postgres worker would claim, never runs
completed work twice, and says exactly where the attempt stands.
"""

import asyncio
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from forge.domain.execution import ExecutionStatus
from forge.domain.risk import RiskLevel
from forge.domain.tool import Idempotency
from forge.infrastructure.clock import SystemClock
from forge.orchestration.missions import cancel_mission
from forge.runtime.agent_loop import ExecutionRunner, claim_next
from forge.runtime.drive import SUPERSEDED, LeaseHeldError, Outcome, claim_task, drive
from forge.security import approvals
from tests.integration.conftest import UowFactory
from tests.runtime_doubles import (
    RecordingTool,
    ScriptedModel,
    SimulatedCrash,
    answer,
    expire_lease,
    runtime,
    submit,
    tool_call,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]
CLOCK = SystemClock()


async def only_task(uow: UowFactory, mission_id: UUID) -> UUID:
    async with uow() as tx:
        (task,) = await tx.tasks.list_for_mission(mission_id)
    return task.id


async def test_driving_a_ready_task_runs_it_and_driving_again_repeats_nothing(
    uow: UowFactory,
) -> None:
    model, tool = ScriptedModel(tool_call(), answer()), RecordingTool()
    task_id = await only_task(uow, await submit(uow))
    rt = runtime(uow, model, tool)

    first = await drive(rt, task_id, 1)
    again = await drive(rt, task_id, 1)

    assert (first.status, again.status) == ("COMPLETED", "COMPLETED")
    assert first.execution_id == again.execution_id
    assert len(model.requests) == 2
    assert len(tool.invocations) == 1


async def test_a_held_lease_is_reported_and_an_expired_one_is_taken_over(
    uow: UowFactory, engine: AsyncEngine
) -> None:
    model = ScriptedModel(tool_call(), answer())
    tool = RecordingTool(behaviours=["crash_after_effect"], idempotency=Idempotency.IDEMPOTENT)
    task_id = await only_task(uow, await submit(uow))
    rt = runtime(uow, model, tool)
    claim = await claim_next(rt)
    assert claim is not None
    with pytest.raises(SimulatedCrash):  # the worker died holding the lease
        await ExecutionRunner(rt, claim).run()

    with pytest.raises(LeaseHeldError):  # it may still be alive: do not touch it
        await drive(runtime(uow, model, tool, worker_id="w2"), task_id, 1)
    await expire_lease(engine, claim.execution.id)
    outcome = await drive(runtime(uow, model, tool, worker_id="w2"), task_id, 1)

    assert outcome.status == "COMPLETED"
    assert sum(tool.effects.values()) == 1  # the idempotent effect happened once


async def test_concurrent_drivers_of_one_attempt_run_it_once(uow: UowFactory) -> None:
    gate = asyncio.Event()

    async def held_open() -> None:
        await gate.wait()

    model = ScriptedModel(tool_call(), answer())
    tool = RecordingTool(on_invoke=held_open)
    task_id = await only_task(uow, await submit(uow))
    first = asyncio.create_task(drive(runtime(uow, model, tool, worker_id="a"), task_id, 1))
    await asyncio.sleep(0.3)

    with pytest.raises(LeaseHeldError):
        await drive(runtime(uow, model, tool, worker_id="b"), task_id, 1)
    gate.set()
    assert (await first).status == "COMPLETED"
    assert len(tool.invocations) == 1


async def test_a_parked_attempt_reports_when_to_look_again_and_resumes_once_decided(
    uow: UowFactory,
) -> None:
    restart = RecordingTool(
        name="deployment.restart", risk=RiskLevel.HIGH, idempotency=Idempotency.NON_IDEMPOTENT
    )
    model = ScriptedModel(tool_call("deployment.restart"), answer("restarted"))
    mission_id = await submit(uow, tools=("deployment.restart",), max_risk=RiskLevel.HIGH)
    task_id = await only_task(uow, mission_id)
    rt = runtime(uow, model, restart)

    parked = await drive(rt, task_id, 1)
    still = await drive(rt, task_id, 1)  # undecided: nothing runs
    async with uow() as tx:
        (approval,) = await tx.approvals.list_approvals()
    await approvals.decide(uow, CLOCK, approval.id, approve=True, operator="a", reason="ok")
    done = await drive(rt, task_id, 1)

    assert parked.parked
    assert parked.wake_at == approval.expires_at
    assert still.parked
    assert done.status == "COMPLETED"
    assert len(restart.invocations) == 1


async def test_a_cancelled_parked_attempt_ends_without_running(uow: UowFactory) -> None:
    restart = RecordingTool(name="deployment.restart", risk=RiskLevel.HIGH)
    mission_id = await submit(uow, tools=("deployment.restart",), max_risk=RiskLevel.HIGH)
    task_id = await only_task(uow, mission_id)
    rt = runtime(uow, ScriptedModel(tool_call("deployment.restart")), restart)
    assert (await drive(rt, task_id, 1)).parked

    await cancel_mission(uow, CLOCK, mission_id)

    assert (await drive(rt, task_id, 1)).status == ExecutionStatus.CANCELLED.value
    assert restart.invocations == []


async def test_a_stale_attempt_is_superseded(uow: UowFactory) -> None:
    task_id = await only_task(uow, await submit(uow))
    rt = runtime(uow, ScriptedModel(answer()), RecordingTool())
    await drive(rt, task_id, 1)

    outcome = await claim_task(rt, task_id, 7)  # an attempt that never existed

    assert isinstance(outcome, Outcome)
    assert outcome.status == SUPERSEDED
