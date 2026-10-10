"""Phase 5: every finished mission gets a structured, persisted evaluation; benchmarks
measure agents against ground truth."""

import asyncio
from uuid import UUID

import httpx2
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from forge.api.app import create_app
from forge.domain.common import DomainError
from forge.domain.evaluation import BenchmarkRunStatus, Dimension, Scope
from forge.domain.mission import MissionStatus
from forge.evaluation import benchmarks
from forge.evaluation.service import evaluate_mission
from forge.evaluation.worker import evaluate_pending
from forge.inference.demo import DemoInvestigationModel
from forge.inference.profiles import DEMO_MODEL, PROFILES
from forge.infrastructure.clock import SystemClock
from forge.orchestration.agents import register_agent
from forge.runtime.agent_loop import Runtime
from forge.runtime.worker import Worker
from forge.tools.demo import MetricsReadTool
from tests.integration.conftest import UowFactory
from tests.runtime_doubles import RecordingTool, ScriptedModel, answer, runtime, submit, tool_call

pytestmark = [pytest.mark.integration, pytest.mark.anyio]
CLOCK = SystemClock()


async def drain(rt: Runtime, uow: UowFactory, mission_ids: list[UUID]) -> None:
    stop = asyncio.Event()
    worker = asyncio.create_task(
        Worker(rt, poll_interval_seconds=0.02, concurrency=3).run_until(stop)
    )
    for _ in range(500):
        async with uow() as tx:
            statuses = [(await tx.missions.get(m)).status for m in mission_ids]
        if all(s.is_terminal for s in statuses):
            break
        await asyncio.sleep(0.02)
    stop.set()
    await worker


async def test_every_finished_mission_is_evaluated_once_with_all_dimensions(
    uow: UowFactory,
) -> None:
    mission_id = await submit(uow)
    await drain(
        runtime(
            uow, ScriptedModel(tool_call(), answer("done 880. Confidence: 0.7")), RecordingTool()
        ),
        uow,
        [mission_id],
    )

    assert await evaluate_pending(uow, CLOCK) == 1
    assert await evaluate_pending(uow, CLOCK) == 0  # never evaluated twice automatically

    async with uow() as tx:
        (evaluation,) = await tx.evaluations.list_for_mission(mission_id)
        events = [e.event_type for e in await tx.events.list_for_mission(mission_id)]
    mission_dimensions = {
        r.result.dimension for r in evaluation.results if r.scope is Scope.MISSION
    }
    task_results = [r for r in evaluation.results if r.scope is Scope.TASK]
    assert mission_dimensions == set(Dimension)
    assert {r.result.dimension for r in task_results} == set(Dimension)
    assert all(r.agent_id is not None and r.execution_id is not None for r in task_results)
    correctness = evaluation.mission_result(Dimension.CORRECTNESS)
    assert correctness is not None
    assert correctness.score is None  # no ground truth: not invented
    assert events[-2:] == ["evaluation.started", "evaluation.completed"]


async def test_failed_missions_are_evaluated_too(uow: UowFactory) -> None:
    mission_id = await submit(uow, budget=1)  # fails on budget before any call
    await drain(runtime(uow, ScriptedModel(), RecordingTool()), uow, [mission_id])

    evaluation = await evaluate_mission(uow, CLOCK, mission_id, trigger="manual")

    assert evaluation is not None
    trajectory = evaluation.mission_result(Dimension.TRAJECTORY_QUALITY)
    assert trajectory is not None
    assert "incomplete" in {f.code for f in trajectory.findings}


async def test_evaluations_are_immutable(uow: UowFactory, engine: AsyncEngine) -> None:
    mission_id = await submit(uow)
    await drain(
        runtime(uow, ScriptedModel(tool_call(), answer()), RecordingTool()), uow, [mission_id]
    )
    await evaluate_mission(uow, CLOCK, mission_id, trigger="manual")

    async with engine.begin() as connection:
        with pytest.raises(DBAPIError, match="append-only"):
            await connection.execute(text("UPDATE evaluation_results SET score = 1"))


async def test_a_benchmark_run_measures_an_agent_against_ground_truth(uow: UowFactory) -> None:
    await benchmarks.register_builtin(uow, CLOCK)
    version = await register_agent(
        uow,
        CLOCK,
        name="baseline",
        role="investigator",
        instructions="Investigate.",
        model=DEMO_MODEL,
        tools=["metrics.read"],
        capabilities=["incident-response"],
        correlation_id=None,
    )
    run = await benchmarks.start_run(
        uow, CLOCK, benchmark="incident-investigation", agent_id=version.agent_id
    )
    rt = Runtime(
        uow_factory=uow,
        clock=CLOCK,
        models={"demo": DemoInvestigationModel()},
        profiles=PROFILES,
        tools={"metrics.read": MetricsReadTool()},
        worker_id="bench",
        lease_seconds=30,
    )
    async with uow() as tx:
        missions = [item.mission_id for item in await tx.benchmarks.run_items(run.id)]
    await drain(rt, uow, missions)

    assert await evaluate_pending(uow, CLOCK) == 3

    async with uow() as tx:
        completed = await tx.benchmarks.get_run(run.id)
    summary = completed.summary
    assert completed.status is BenchmarkRunStatus.COMPLETED
    # The demo agent always investigates checkout: right once, wrong for payments and inventory.
    assert summary["cases"] == 3
    assert summary["passed"] == 1
    per_case = summary["per_case"]
    assert isinstance(per_case, dict)
    passed = {key: case["passed"] for key, case in per_case.items() if isinstance(case, dict)}
    assert passed == {
        "checkout-regression": True,
        "payments-steady": False,
        "inventory-spike": False,
    }
    assert summary["expected_calibration_error"] is not None
    assert isinstance(summary["cost_per_success_micro_usd"], int)


async def test_recording_a_run_requires_exactly_one_mission_per_case(uow: UowFactory) -> None:
    await benchmarks.register_builtin(uow, CLOCK)
    async with uow() as tx:
        definition = await tx.benchmarks.get("incident-investigation", 1)
    version = await register_agent(
        uow,
        CLOCK,
        name="recorder",
        role="investigator",
        instructions="Investigate.",
        model=DEMO_MODEL,
        tools=["metrics.read"],
        capabilities=["incident-response"],
        correlation_id=None,
    )
    mission = await submit(uow)
    keys = [case.key for case in definition.cases]

    for missions in ({keys[0]: mission}, {**dict.fromkeys(keys, mission), "unknown": mission}):
        with pytest.raises(DomainError, match="one mission per case"):
            await benchmarks.record_run(
                uow, CLOCK, definition, agent_id=version.agent_id, missions=missions
            )
    async with uow() as tx:
        assert await tx.benchmarks.list_runs(limit=10) == []


async def test_evaluation_api(uow: UowFactory) -> None:
    mission_id = await submit(uow)
    app = create_app(uow_factory=uow, clock=CLOCK, readiness_checks=(), readiness_timeout_seconds=1)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://f"
    ) as api:
        too_early = await api.post(f"/missions/{mission_id}/evaluations")
        await drain(
            runtime(uow, ScriptedModel(tool_call(), answer()), RecordingTool()), uow, [mission_id]
        )
        created = await api.post(f"/missions/{mission_id}/evaluations")
        listed = (await api.get(f"/missions/{mission_id}/evaluations")).json()
        catalogue = (await api.get("/benchmarks")).json()

    assert too_early.status_code == 409
    assert created.status_code == 201
    assert len(listed) == 1
    assert {r["dimension"] for r in listed[0]["results"]} == {d.value for d in Dimension}
    assert [b["name"] for b in catalogue] == ["incident-investigation"]
    async with uow() as tx:
        assert (await tx.missions.get(mission_id)).status is MissionStatus.COMPLETED
