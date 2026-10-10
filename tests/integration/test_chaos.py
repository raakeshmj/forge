"""Seeded chaos: many missions, several workers, crashes at random recovery points,
database outages, and contention, then the invariants that must survive all of it
(ADR 0011). A failing seed reproduces exactly: rerun with that seed.

In-process crashes (SimulatedCrash) still run the worker's `finally`, so they release
the lease like a graceful death; lease expiry after a hard kill is covered by
test_process_crashes.py.
"""

import asyncio
import random
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import replace
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from forge.domain.common import JSONValue
from forge.domain.cost import Usage
from forge.domain.execution import ExecutionStatus, FailureKind
from forge.domain.model import ModelRequest, ModelResponse, TextBlock, ToolResultBlock, ToolUseBlock
from forge.domain.risk import RiskLevel
from forge.domain.tool import Idempotency, ToolDefinition, ToolResult
from forge.infrastructure.clock import SystemClock
from forge.runtime.agent_loop import Runtime
from forge.runtime.worker import Worker
from forge.tools.base import ToolContext
from tests.integration.conftest import UowFactory
from tests.runtime_doubles import (
    FAST_POLICY,
    TEST_MODEL,
    TEST_PROFILE,
    Outage,
    SimulatedCrash,
    submit,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

MISSIONS = 8
WORKERS = 3
STEPS = 3  # reads before the write
CRASH_RATE = 0.15
LEASE_SECONDS = 0.4
# Failures chaos may legitimately cause; anything else is a bug.
EXPECTED_FAILURES = {FailureKind.TOOL_OUTCOME_UNKNOWN, FailureKind.RECOVERY_EXHAUSTED}


class Chaos:
    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)  # noqa: S311 - reproducible chaos, not cryptography
        self.crashes = 0

    def maybe_crash(self) -> None:
        if self.rng.random() < CRASH_RATE:
            self.crashes += 1
            raise SimulatedCrash


class ChaosModel:
    """Stateless: read STEPS times, write once, answer. May die before answering."""

    def __init__(self, chaos: Chaos) -> None:
        self._chaos = chaos

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self._chaos.maybe_crash()
        results = sum(isinstance(b, ToolResultBlock) for m in request.messages for b in m.content)
        usage = Usage(input_tokens=10, output_tokens=5)
        if results < STEPS:
            call = ToolUseBlock(f"r{results}", "metrics.read", {"n": results})
        elif results == STEPS:
            call = ToolUseBlock("w", "ledger.append", {"entry": "x"})
        else:
            return ModelResponse((TextBlock("done"),), "end_turn", usage)
        return ModelResponse((TextBlock("working"), call), "tool_use", usage)


class ChaosTool:
    def __init__(self, name: str, idempotency: Idempotency, chaos: Chaos) -> None:
        self._chaos = chaos
        self.effects: Counter[str] = Counter()
        self.definition = ToolDefinition(
            name=name,
            version="1",
            description=name,
            input_schema={"type": "object"},
            risk=RiskLevel.LOW,
            idempotency=idempotency,
            timeout_seconds=5.0,
        )

    async def invoke(self, arguments: Mapping[str, JSONValue], context: ToolContext) -> ToolResult:
        self._chaos.maybe_crash()  # before the effect
        self.effects[context.idempotency_key] += 1
        self._chaos.maybe_crash()  # after the effect, before its outcome is recorded
        return ToolResult("ok")


async def supervise(rt_for: "RuntimeFactory", stop: asyncio.Event) -> int:
    """Keep WORKERS workers alive; a crashed worker is replaced by a new process id."""
    generation = 0

    async def one(slot: int) -> None:
        nonlocal generation
        while not stop.is_set():
            generation += 1
            try:
                await Worker(rt_for(f"w{slot}-{generation}"), poll_interval_seconds=0.02).run_until(
                    stop
                )
            except BaseExceptionGroup as group:  # SimulatedCrash: this "process" died
                if not group.subgroup(SimulatedCrash):
                    raise

    async with asyncio.TaskGroup() as tasks:
        for slot in range(WORKERS):
            tasks.create_task(one(slot))
    return generation


class RuntimeFactory:
    def __init__(self, uow: Outage, chaos: Chaos, read: ChaosTool, write: ChaosTool) -> None:
        self._uow, self._chaos, self._read, self._write = uow, chaos, read, write

    def __call__(self, worker_id: str) -> Runtime:
        return Runtime(
            uow_factory=self._uow,  # type: ignore[arg-type]
            clock=SystemClock(),
            models={TEST_MODEL.provider: ChaosModel(self._chaos)},
            profiles={TEST_MODEL: TEST_PROFILE},
            tools={"metrics.read": self._read, "ledger.append": self._write},
            worker_id=worker_id,
            lease_seconds=LEASE_SECONDS,
            max_attempts=8,
        )


async def outages(outage: Outage, rng: random.Random, stop: asyncio.Event) -> None:
    while not stop.is_set():
        await asyncio.sleep(rng.uniform(0.3, 0.8))
        outage.down = True
        await asyncio.sleep(rng.uniform(0.05, 0.25))  # shorter than a lease
        outage.down = False


async def all_settled(uow: UowFactory, mission_ids: list[UUID], attempts: int = 1200) -> None:
    for _ in range(attempts):
        async with uow() as tx:
            statuses = [(await tx.missions.get(m)).status for m in mission_ids]
        if all(s.is_terminal for s in statuses):
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"missions did not settle: {statuses}")


@pytest.mark.parametrize("seed", [11, 23, 42])
async def test_invariants_hold_under_crashes_outages_and_contention(
    seed: int, uow: UowFactory, engine: AsyncEngine
) -> None:
    chaos = Chaos(seed)
    read = ChaosTool("metrics.read", Idempotency.READ_ONLY, chaos)
    write = ChaosTool("ledger.append", Idempotency.NON_IDEMPOTENT, chaos)
    mission_ids = [
        await submit(uow, tools=("metrics.read", "ledger.append")) for _ in range(MISSIONS)
    ]
    outage = Outage(uow)
    stop = asyncio.Event()
    background = [
        asyncio.create_task(supervise(RuntimeFactory(outage, chaos, read, write), stop)),
        asyncio.create_task(outages(outage, random.Random(seed + 1), stop)),  # noqa: S311
    ]
    try:
        await all_settled(uow, mission_ids)
    finally:
        stop.set()
        await asyncio.gather(*background)

    assert chaos.crashes > 0  # the run was actually chaotic
    executions = []
    async with uow() as tx:
        for mission_id in mission_ids:
            executions.extend(await tx.executions.list_for_mission(mission_id))
    # 1. Every execution ended; none is left running or parked.
    assert all(e.status in {ExecutionStatus.COMPLETED, ExecutionStatus.FAILED} for e in executions)
    # 2. The non-idempotent write never happened twice for one call.
    assert all(count == 1 for count in write.effects.values()), write.effects
    # 3. Every completed execution wrote exactly once; failures are only the expected kinds.
    for execution in executions:
        writes = [k for k in write.effects if k.startswith(f"{execution.id}:")]
        if execution.status is ExecutionStatus.COMPLETED:
            assert len(writes) == 1
        else:
            assert execution.failure_kind in EXPECTED_FAILURES, execution.failure_detail
            assert len(writes) <= 1
    async with engine.connect() as connection:
        # 4. Event sequences per aggregate have no gaps and no duplicates.
        gaps = await connection.scalar(
            text(
                "SELECT count(*) FROM (SELECT aggregate_id, count(*) AS n, max(sequence) AS top "
                "FROM events GROUP BY aggregate_id) s WHERE n <> top"
            )
        )
        # 5. Checkpoints per execution are contiguous.
        holes = await connection.scalar(
            text(
                "SELECT count(*) FROM (SELECT execution_id, count(*) AS n, max(sequence) AS top "
                "FROM checkpoints GROUP BY execution_id) s WHERE n <> top"
            )
        )
    assert gaps == 0
    assert holes == 0


class LongModel:
    """Reads `total` times, then answers; dies once at each step listed in `crash_at`."""

    def __init__(self, total: int, crash_at: set[int]) -> None:
        self._total = total
        self._crash_at = crash_at

    async def complete(self, request: ModelRequest) -> ModelResponse:
        done = sum(isinstance(b, ToolResultBlock) for m in request.messages for b in m.content)
        if done in self._crash_at:
            self._crash_at.discard(done)
            raise SimulatedCrash
        usage = Usage(input_tokens=10, output_tokens=5)
        if done >= self._total:
            return ModelResponse((TextBlock(f"read {done} times"),), "end_turn", usage)
        call = ToolUseBlock(f"r{done}", "metrics.read", {"n": done})
        return ModelResponse((TextBlock("reading"), call), "tool_use", usage)


class SlowRead:
    def __init__(self) -> None:
        self.calls: Counter[str] = Counter()
        self.definition = ToolDefinition(
            name="metrics.read",
            version="1",
            description="read",
            input_schema={"type": "object"},
            risk=RiskLevel.LOW,
            idempotency=Idempotency.READ_ONLY,
            timeout_seconds=5.0,
        )

    async def invoke(self, arguments: Mapping[str, JSONValue], context: ToolContext) -> ToolResult:
        self.calls[context.idempotency_key] += 1
        await asyncio.sleep(0.05)
        return ToolResult("ok")


async def test_a_long_mission_outlives_many_leases_and_several_crashes(
    uow: UowFactory, engine: AsyncEngine
) -> None:
    total = 30
    model, read = LongModel(total, {7, 15, 23}), SlowRead()
    mission_id = await submit(
        uow, tools=("metrics.read",), policy=replace(FAST_POLICY, max_steps=total + 1)
    )

    def rt(worker_id: str) -> Runtime:
        return Runtime(
            uow_factory=uow,
            clock=SystemClock(),
            models={TEST_MODEL.provider: model},
            profiles={TEST_PROFILE.model: TEST_PROFILE},
            tools={"metrics.read": read},
            worker_id=worker_id,
            lease_seconds=0.2,  # the mission runs for many lease periods
        )

    stop = asyncio.Event()
    supervisor = asyncio.create_task(_supervise_one(rt, stop))
    try:
        await all_settled(uow, [mission_id])
    finally:
        stop.set()
        await supervisor

    async with uow() as tx:
        (execution,) = await tx.executions.list_for_mission(mission_id)
        checkpoints = await tx.checkpoints.list(execution.id)
    assert execution.status is ExecutionStatus.COMPLETED, execution.failure_detail
    assert execution.output == f"read {total} times"
    assert execution.attempt == 4  # three crashes, three recoveries
    assert len(read.calls) == total
    assert all(n == 1 for n in read.calls.values())  # no completed read was redone
    assert [c.sequence for c in checkpoints] == list(range(1, len(checkpoints) + 1))


async def _supervise_one(rt_for: Callable[[str], Runtime], stop: asyncio.Event) -> None:
    generation = 0
    while not stop.is_set():
        generation += 1
        try:
            await Worker(rt_for(f"long-{generation}"), poll_interval_seconds=0.02).run_until(stop)
        except BaseExceptionGroup as group:
            if not group.subgroup(SimulatedCrash):
                raise
