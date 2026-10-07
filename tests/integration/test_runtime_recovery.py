"""Worker interruption and recovery: resume from the last checkpoint, never repeating done work.

Crashes are injected with SimulatedCrash, which escapes every `except Exception` and
leaves the lease held and the open transaction uncommitted, exactly as a dead process would.
"""

import asyncio
from dataclasses import replace
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from forge.domain.common import JSONObject
from forge.domain.execution import Execution, ExecutionStatus, FailureKind
from forge.domain.mission import MissionStatus
from forge.domain.repositories import LeaseLostError
from forge.domain.tool import Idempotency, idempotency_key
from forge.runtime.agent_loop import ExecutionRunner, Runtime, claim_next
from forge.runtime.worker import Worker
from tests.integration.conftest import UowFactory
from tests.runtime_doubles import (
    CALL_COST,
    RecordingTool,
    ScriptedModel,
    SimulatedCrash,
    answer,
    crash,
    expire_lease,
    runtime,
    submit,
    tool_call,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def crash_first_attempt(rt: Runtime) -> UUID:
    claim = await claim_next(rt)
    assert claim is not None
    with pytest.raises(SimulatedCrash):
        await ExecutionRunner(rt, claim).run()
    return claim.execution.id


async def resume(rt: Runtime) -> Execution:
    claim = await claim_next(rt)
    assert claim is not None, "the interrupted execution should be reclaimable"
    final = await Worker(rt, poll_interval_seconds=0.01).run_claim(claim)
    assert final is not None
    return final


async def history(uow: UowFactory, mission_id: UUID) -> list[tuple[str, JSONObject]]:
    async with uow() as tx:
        return [(e.event_type, e.payload) for e in await tx.events.list_for_mission(mission_id)]


async def assert_contiguous_checkpoints(uow: UowFactory, execution_id: UUID) -> None:
    async with uow() as tx:
        sequences = [c.sequence for c in await tx.checkpoints.list(execution_id)]
    assert sequences == list(range(1, len(sequences) + 1))


async def test_crash_during_a_model_call_repeats_only_that_call(
    uow: UowFactory, engine: AsyncEngine
) -> None:
    model = ScriptedModel(crash, tool_call(), answer())
    tool = RecordingTool()
    mission_id = await submit(uow)

    execution_id = await crash_first_attempt(runtime(uow, model, tool, worker_id="w1"))
    await expire_lease(engine, execution_id)
    final = await resume(runtime(uow, model, tool, worker_id="w2"))

    assert final.status is ExecutionStatus.COMPLETED
    assert final.attempt == 2
    assert len(model.requests) == 3  # the unrecorded call is the only one repeated
    assert len(tool.invocations) == 1
    assert final.cost_micro_usd == 2 * CALL_COST + 7  # the lost call was never recorded
    events = await history(uow, mission_id)
    assert [p["attempt"] for t, p in events if t == "agent.started"] == [1, 2]
    assert [p["worker_id"] for t, p in events if t == "agent.started"] == ["w1", "w2"]
    await assert_contiguous_checkpoints(uow, final.id)


async def test_recorded_model_turn_is_not_repeated_and_read_only_tool_reruns(
    uow: UowFactory, engine: AsyncEngine
) -> None:
    model = ScriptedModel(tool_call(), answer())
    tool = RecordingTool(behaviours=["crash_after_effect"])
    mission_id = await submit(uow)

    execution_id = await crash_first_attempt(runtime(uow, model, tool))
    await expire_lease(engine, execution_id)
    final = await resume(runtime(uow, model, tool, worker_id="w2"))

    assert final.status is ExecutionStatus.COMPLETED
    assert len(model.requests) == 2  # turn 0 was committed before the crash
    key = idempotency_key(final.id, 0, "c1")
    assert tool.invocations == [key, key]  # read-only: safe to repeat with the same key
    events = await history(uow, mission_id)
    assert [p["invocation"] for t, p in events if t == "tool.called"] == [1, 2]
    assert [t for t, _ in events].count("model.called") == 2
    await assert_contiguous_checkpoints(uow, final.id)


async def test_idempotent_side_effect_happens_once_across_a_crash(
    uow: UowFactory, engine: AsyncEngine
) -> None:
    model = ScriptedModel(tool_call(), answer())
    tool = RecordingTool(idempotency=Idempotency.IDEMPOTENT, behaviours=["crash_after_effect"])
    await submit(uow)

    execution_id = await crash_first_attempt(runtime(uow, model, tool))
    await expire_lease(engine, execution_id)
    final = await resume(runtime(uow, model, tool, worker_id="w2"))

    assert final.status is ExecutionStatus.COMPLETED
    key = idempotency_key(final.id, 0, "c1")
    assert tool.invocations == [key, key]  # re-executed with the same key...
    assert tool.effects == {key: 1}  # ...which deduplicated the side effect


async def test_non_idempotent_tool_is_never_repeated_after_a_crash(
    uow: UowFactory, engine: AsyncEngine
) -> None:
    model = ScriptedModel(tool_call(), answer())
    tool = RecordingTool(idempotency=Idempotency.NON_IDEMPOTENT, behaviours=["crash_after_effect"])
    mission_id = await submit(uow)

    execution_id = await crash_first_attempt(runtime(uow, model, tool))
    await expire_lease(engine, execution_id)
    final = await resume(runtime(uow, model, tool, worker_id="w2"))

    key = idempotency_key(final.id, 0, "c1")
    assert final.status is ExecutionStatus.FAILED
    assert final.failure_kind is FailureKind.TOOL_OUTCOME_UNKNOWN
    assert tool.invocations == [key]
    assert tool.effects == {key: 1}
    assert len(model.requests) == 1
    async with uow() as tx:
        assert (await tx.missions.get(mission_id)).status is MissionStatus.FAILED


async def test_recorded_tool_result_is_not_repeated(uow: UowFactory, engine: AsyncEngine) -> None:
    model = ScriptedModel(tool_call(), crash, answer())
    tool = RecordingTool(idempotency=Idempotency.NON_IDEMPOTENT)

    await submit(uow)
    execution_id = await crash_first_attempt(runtime(uow, model, tool))
    await expire_lease(engine, execution_id)
    final = await resume(runtime(uow, model, tool, worker_id="w2"))

    assert final.status is ExecutionStatus.COMPLETED
    assert len(tool.invocations) == 1  # even a non-idempotent tool is safe once recorded
    assert len(model.requests) == 3
    await assert_contiguous_checkpoints(uow, final.id)


async def test_a_stale_worker_cannot_commit_after_its_lease_is_taken_over(
    uow: UowFactory, engine: AsyncEngine
) -> None:
    gate, reached = asyncio.Event(), asyncio.Event()

    async def first_call_stalls() -> None:
        if not reached.is_set():
            reached.set()
            await gate.wait()

    model = ScriptedModel(tool_call(), answer())
    tool = RecordingTool(on_invoke=first_call_stalls)
    mission_id = await submit(uow)

    stale_rt = runtime(uow, model, tool, worker_id="stale")
    claim = await claim_next(stale_rt)
    assert claim is not None
    stale = asyncio.create_task(ExecutionRunner(stale_rt, claim).run())
    await reached.wait()

    await expire_lease(engine, claim.execution.id)
    final = await resume(runtime(uow, model, tool, worker_id="fresh"))
    gate.set()

    with pytest.raises(LeaseLostError):
        await stale
    assert final.status is ExecutionStatus.COMPLETED
    events = await history(uow, mission_id)
    assert [t for t, _ in events].count("agent.completed") == 1
    assert [t for t, _ in events].count("tool.completed") == 1  # the stale result was discarded
    await assert_contiguous_checkpoints(uow, final.id)


async def test_concurrent_workers_start_a_task_exactly_once(uow: UowFactory) -> None:
    await submit(uow)
    workers = [runtime(uow, ScriptedModel(), worker_id=f"w{i}") for i in range(5)]

    claims = await asyncio.gather(*(claim_next(rt) for rt in workers))

    assert sum(claim is not None for claim in claims) == 1


async def test_repeated_crashes_exhaust_recovery(uow: UowFactory, engine: AsyncEngine) -> None:
    model = ScriptedModel(crash, crash, answer())
    limited = replace(runtime(uow, model, RecordingTool()), max_attempts=2)
    mission_id = await submit(uow)

    for _ in range(2):
        execution_id = await crash_first_attempt(limited)
        await expire_lease(engine, execution_id)
    final = await resume(limited)

    assert final.status is ExecutionStatus.FAILED
    assert final.failure_kind is FailureKind.RECOVERY_EXHAUSTED
    assert final.attempt == 3
    async with uow() as tx:
        assert (await tx.missions.get(mission_id)).status is MissionStatus.FAILED


async def test_shutdown_releases_the_lease_for_immediate_resume(uow: UowFactory) -> None:
    reached = asyncio.Event()

    async def signal_reached() -> None:
        reached.set()

    model = ScriptedModel(tool_call(), answer())
    tool = RecordingTool(behaviours=["hang"], on_invoke=signal_reached)
    await submit(uow)

    stop = asyncio.Event()
    worker = asyncio.create_task(
        Worker(runtime(uow, model, tool), poll_interval_seconds=0.01).run_until(stop)
    )
    await reached.wait()
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker

    final = await resume(runtime(uow, model, tool, worker_id="w2"))  # no lease expiry needed
    assert final.status is ExecutionStatus.COMPLETED
    assert final.attempt == 2


async def test_heartbeat_keeps_a_long_step_leased(uow: UowFactory) -> None:
    async def slow() -> None:
        await asyncio.sleep(1.0)

    model = ScriptedModel(tool_call(), answer())
    tool = RecordingTool(on_invoke=slow)
    await submit(uow)
    rt = runtime(uow, model, tool, lease_seconds=0.3)  # far shorter than the step

    claim = await claim_next(rt)
    assert claim is not None
    running = asyncio.create_task(Worker(rt, poll_interval_seconds=0.01).run_claim(claim))
    for _ in range(8):  # while the step runs, nobody else can take the execution
        await asyncio.sleep(0.1)
        assert await claim_next(runtime(uow, model, tool, worker_id="thief")) is None
    final = await running

    assert final is not None
    assert final.status is ExecutionStatus.COMPLETED
    assert final.attempt == 1
