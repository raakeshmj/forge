"""Persistence: immutability, optimistic concurrency, lease fencing, skip-locked claims."""

from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from forge.domain.agent import Agent, AgentVersion
from forge.domain.checkpoint import AgentState, Checkpoint
from forge.domain.cost import Usage
from forge.domain.events import MissionCreated, MissionStarted
from forge.domain.execution import Execution, ExecutionStatus
from forge.domain.mission import Mission, MissionStatus
from forge.domain.repositories import ConcurrentUpdateError, LeaseLostError
from forge.domain.task import Task, TaskStatus
from forge.domain.tool import InvocationStatus, ToolInvocation
from tests import factories
from tests.factories import NOW
from tests.integration.conftest import UowFactory

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def _seed(
    uow: UowFactory, task_status: TaskStatus = TaskStatus.READY
) -> tuple[Agent, AgentVersion, Mission, Task]:
    agent, version = factories.agent_and_version()
    mission = factories.mission(status=MissionStatus.READY)
    task = factories.task(mission_id=mission.id, agent_id=agent.id, status=task_status)
    async with uow() as tx:
        await tx.agents.add(agent, version)
        await tx.missions.add(mission)
        await tx.tasks.add(task)
    return agent, version, mission, task


def _execution(mission_id: UUID, task_id: UUID, agent_id: UUID, version_id: UUID) -> Execution:
    return Execution(
        id=uuid4(),
        mission_id=mission_id,
        task_id=task_id,
        agent_id=agent_id,
        agent_version_id=version_id,
        model=factories.agent_and_version()[1].model,
        status=ExecutionStatus.RUNNING,
        attempt=1,
        usage=Usage(),
        cost_micro_usd=0,
        output=None,
        failure_kind=None,
        failure_detail=None,
        started_at=NOW,
        completed_at=None,
    )


async def _expire_lease(engine: AsyncEngine, execution_id: UUID) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE executions SET lease_expires_at = now() - interval '1s' WHERE id = :id"),
            {"id": execution_id},
        )


async def test_round_trips_every_aggregate(uow: UowFactory) -> None:
    agent, version, mission, task = await _seed(uow)

    async with uow() as tx:
        assert await tx.agents.get(agent.id) == agent
        assert await tx.agents.get_current_version(agent.id) == version
        assert await tx.missions.get(mission.id) == mission
        assert await tx.tasks.get(task.id) == task


async def test_events_get_per_aggregate_sequences(uow: UowFactory) -> None:
    _, _, mission, _ = await _seed(uow)

    async with uow() as tx:
        first = await tx.events.append(
            MissionCreated(
                aggregate_id=mission.id,
                mission_id=mission.id,
                agent_id=uuid4(),
                budget_micro_usd=1,
                max_risk="LOW",
                deadline=None,
            ),
            correlation_id="c",
        )
        second = await tx.events.append(
            MissionStarted(aggregate_id=mission.id, mission_id=mission.id),
            correlation_id="c",
            causation_id=first.id,
        )

    assert (first.sequence, second.sequence) == (1, 2)
    async with uow() as tx:
        stored = await tx.events.list_for_mission(mission.id)
    assert [e.event_type for e in stored] == ["mission.created", "mission.started"]
    assert stored[1].causation_id == first.id
    assert stored[0].payload["max_risk"] == "LOW"


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE events SET event_type = 'tampered'",
        "DELETE FROM events",
        "TRUNCATE events",
        "UPDATE checkpoints SET state_version = 99",
        "DELETE FROM agent_versions",
    ],
)
async def test_history_tables_reject_mutation(
    uow: UowFactory, engine: AsyncEngine, statement: str
) -> None:
    agent, version, mission, task = await _seed(uow)
    execution = _execution(mission.id, task.id, agent.id, version.id)
    async with uow() as tx:
        await tx.events.append(
            MissionStarted(aggregate_id=mission.id, mission_id=mission.id), correlation_id=None
        )
        await tx.executions.add(execution, worker_id="w", lease_seconds=30)
        await tx.checkpoints.add(Checkpoint(execution.id, 1, AgentState.initial("o"), 1, NOW))

    async with engine.begin() as connection:
        with pytest.raises(DBAPIError, match="append-only"):
            await connection.execute(text(statement))


async def test_stale_mission_update_is_rejected(uow: UowFactory) -> None:
    _, _, mission, _ = await _seed(uow)

    async with uow() as tx:
        await tx.missions.update(mission.transition(MissionStatus.RUNNING, NOW))
    with pytest.raises(ConcurrentUpdateError):
        async with uow() as tx:
            await tx.missions.update(mission.transition(MissionStatus.CANCELLED, NOW))


async def test_unit_of_work_rolls_back_on_error(uow: UowFactory) -> None:
    _, _, mission, _ = await _seed(uow)

    async def update_then_fail() -> None:
        async with uow() as tx:
            await tx.missions.update(mission.transition(MissionStatus.RUNNING, NOW))
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await update_then_fail()

    async with uow() as tx:
        assert (await tx.missions.get(mission.id)).status is MissionStatus.READY


async def test_only_the_current_lease_holder_can_write(
    uow: UowFactory, engine: AsyncEngine
) -> None:
    agent, version, mission, task = await _seed(uow)
    execution = _execution(mission.id, task.id, agent.id, version.id)
    async with uow() as tx:
        fence = await tx.executions.add(execution, worker_id="a", lease_seconds=30)

    async with uow() as tx:  # lease still valid: nothing to reclaim
        assert await tx.executions.reclaim_expired(worker_id="b", lease_seconds=30) is None

    await _expire_lease(engine, execution.id)
    async with uow() as tx:
        reclaimed = await tx.executions.reclaim_expired(worker_id="b", lease_seconds=30)
    assert reclaimed is not None
    taken_over, new_fence = reclaimed
    assert (taken_over.attempt, new_fence) == (2, fence + 1)

    with pytest.raises(LeaseLostError):
        async with uow() as tx:
            await tx.executions.update(
                execution.record_spend(Usage(1, 1), 5), fence=fence, lease_seconds=30
            )
    async with uow() as tx:
        assert not await tx.executions.renew(execution.id, fence=fence, lease_seconds=30)
        await tx.executions.update(
            taken_over.record_spend(Usage(1, 1), 5), fence=new_fence, lease_seconds=30
        )
        assert await tx.executions.mission_spend(mission.id) == 5


async def test_terminal_execution_cannot_be_written_again(uow: UowFactory) -> None:
    agent, version, mission, task = await _seed(uow)
    execution = _execution(mission.id, task.id, agent.id, version.id)
    async with uow() as tx:
        fence = await tx.executions.add(execution, worker_id="a", lease_seconds=30)
        await tx.executions.update(execution.complete("done", NOW), fence=fence, lease_seconds=30)

    with pytest.raises(LeaseLostError):
        async with uow() as tx:
            await tx.executions.update(
                replace(execution, output="again"), fence=fence, lease_seconds=30
            )


async def test_concurrent_claimers_never_lock_the_same_task(uow: UowFactory) -> None:
    *_, task = await _seed(uow)

    async with uow() as first, uow() as second:
        claimed_first = await first.tasks.lock_next_ready()
        claimed_second = await second.tasks.lock_next_ready()

    assert claimed_first is not None
    assert claimed_first.id == task.id
    assert claimed_second is None


async def test_tool_ledger_counts_invocations_per_key(uow: UowFactory) -> None:
    agent, version, mission, task = await _seed(uow)
    execution = _execution(mission.id, task.id, agent.id, version.id)
    intent = ToolInvocation(
        "k1", execution.id, 0, "t1", "metrics.read", "1", InvocationStatus.INTENDED, 1
    )
    async with uow() as tx:
        await tx.executions.add(execution, worker_id="a", lease_seconds=30)
        assert (await tx.tool_invocations.record_intent(intent)).invocations == 1
        assert (await tx.tool_invocations.record_intent(intent)).invocations == 2
        await tx.tool_invocations.record_outcome("k1", InvocationStatus.SUCCEEDED)
        stored = await tx.tool_invocations.get("k1")

    assert stored is not None
    assert (stored.status, stored.invocations) == (InvocationStatus.SUCCEEDED, 2)
