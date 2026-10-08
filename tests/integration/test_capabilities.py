"""Phase 6: evaluations become competency evidence, and routing follows measured ability."""

import asyncio
from uuid import UUID

import httpx2
import pytest

from forge.api.app import create_app
from forge.capabilities import engine
from forge.domain.agent import ModelRef
from forge.domain.mission import Budget, RiskPolicy
from forge.domain.plan import PlannedTask
from forge.domain.risk import RiskLevel
from forge.evaluation import benchmarks
from forge.evaluation.worker import evaluate_pending
from forge.inference.demo import DemoInvestigationModel
from forge.inference.profiles import DEMO_MODEL, PROFILES
from forge.infrastructure.clock import SystemClock
from forge.orchestration.agents import register_agent
from forge.orchestration.missions import MissionRequest, submit_mission
from forge.runtime.agent_loop import Runtime
from forge.runtime.worker import Worker
from forge.tools.demo import MetricsReadTool
from tests.integration.conftest import UowFactory
from tests.runtime_doubles import TEST_MODEL, TEST_PROFILE, OracleInvestigator

pytestmark = [pytest.mark.integration, pytest.mark.anyio]
CLOCK = SystemClock()


def runtime(uow: UowFactory) -> Runtime:
    return Runtime(
        uow_factory=uow,
        clock=CLOCK,
        models={"demo": DemoInvestigationModel(), "test": OracleInvestigator()},
        profiles={**PROFILES, TEST_MODEL: TEST_PROFILE},
        tools={"metrics.read": MetricsReadTool()},
        worker_id="cap",
        lease_seconds=30,
    )


async def settle(uow: UowFactory, missions: list[UUID]) -> None:
    stop = asyncio.Event()
    worker = asyncio.create_task(
        Worker(runtime(uow), poll_interval_seconds=0.02, concurrency=3).run_until(stop)
    )
    for _ in range(500):
        async with uow() as tx:
            statuses = [(await tx.missions.get(m)).status for m in missions]
        if all(status.is_terminal for status in statuses):
            break
        await asyncio.sleep(0.02)
    stop.set()
    await worker
    while await evaluate_pending(uow, CLOCK):
        pass


async def benchmarked_agent(uow: UowFactory, name: str, model: ModelRef) -> UUID:
    version = await register_agent(
        uow,
        CLOCK,
        name=name,
        role="investigator",
        instructions="Investigate.",
        model=model,
        tools=["metrics.read"],
        capabilities=["observability"],
        correlation_id=None,
    )
    run = await benchmarks.start_run(
        uow, CLOCK, benchmark="incident-investigation", agent_id=version.agent_id
    )
    async with uow() as tx:
        missions = [item.mission_id for item in await tx.benchmarks.run_items(run.id)]
    await settle(uow, missions)
    return version.agent_id


async def test_benchmark_evidence_becomes_competency_and_steers_routing(uow: UowFactory) -> None:
    await engine.register_builtin(uow, CLOCK)
    await benchmarks.register_builtin(uow, CLOCK)
    weak = await benchmarked_agent(uow, "always-checkout", DEMO_MODEL)
    strong = await benchmarked_agent(uow, "oracle", TEST_MODEL)

    async with uow() as tx:
        competencies = {
            (c.agent_id, c.capability): c for c in await tx.competencies.for_agents([weak, strong])
        }
    weak_ir, strong_ir = (
        competencies[(weak, "incident-response")],
        competencies[(strong, "incident-response")],
    )
    assert weak_ir.sample_count == strong_ir.sample_count == 3
    assert strong_ir.estimated_score > weak_ir.estimated_score
    assert weak_ir.uncertainty < 0.29  # evidence narrowed the prior
    assert weak_ir.calibration is not None
    assert weak_ir.calibration < strong_ir.calibration  # type: ignore[operator]  # claims 0.8, often wrong

    request = MissionRequest(
        objective="Investigate the checkout slowdown",
        budget=Budget(10**6),
        tasks=(
            PlannedTask("investigate", "investigate", required_capabilities=("incident-response",)),
        ),
        risk_policy=RiskPolicy(RiskLevel.MEDIUM),  # risky work: prefer proven ability
    )
    mission = await submit_mission(uow, CLOCK, request, correlation_id="routing")
    async with uow() as tx:
        (task,) = await tx.tasks.list_for_mission(mission.id)
        routed = [
            e.payload
            for e in await tx.events.list_for_mission(mission.id)
            if e.event_type == "task.routed"
        ]
    assert task.agent_id == strong
    candidates = routed[0]["candidates"]
    assert isinstance(candidates, list)
    assert str(candidates[0]).startswith("selected oracle: fitness")
    assert "always-checkout: fitness" in str(candidates[1])


async def test_agent_detail_exposes_competency_reliability_and_the_graph(uow: UowFactory) -> None:
    await engine.register_builtin(uow, CLOCK)
    await benchmarks.register_builtin(uow, CLOCK)
    agent_id = await benchmarked_agent(uow, "oracle", TEST_MODEL)
    app = create_app(uow_factory=uow, clock=CLOCK, readiness_checks=(), readiness_timeout_seconds=1)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://f"
    ) as api:
        detail = (await api.get(f"/agents/{agent_id}")).json()
        graph = (await api.get("/capabilities")).json()
        listed = (await api.get("/agents")).json()

    competency = {c["capability"]: c for c in detail["competencies"]}
    assert competency["incident-response"]["sample_count"] == 3
    assert competency["incident-response"]["uncertainty"] > 0
    assert len(competency["incident-response"]["supporting_evaluations"]) == 3
    assert detail["reliability"]["successes"] == 3
    assert detail["reliability"]["mean_latency_ms"] is not None
    assert {c["name"] for c in graph} >= {"incident-response", "observability"}
    assert [a["name"] for a in listed] == ["oracle"]
