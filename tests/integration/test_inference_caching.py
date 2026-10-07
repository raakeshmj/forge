"""Phase 2 Definition of Done: a repeated mission demonstrates measurable context-cache reuse.

Uses the deterministic `demo` provider, which simulates provider-native prefix caching
(breakpoints, minimum cacheable size, TTL). Live-provider verification is in tests/live.
"""

from uuid import UUID

import pytest

from forge.domain.agent import ModelRef
from forge.domain.common import JSONObject
from forge.domain.execution import ExecutionStatus, FailureKind
from forge.domain.mission import Budget
from forge.inference.demo import DemoInvestigationModel
from forge.inference.profiles import DEMO_MODEL, PROFILES
from forge.infrastructure.clock import SystemClock
from forge.orchestration.agents import register_agent
from forge.orchestration.missions import MissionRequest, submit_mission
from forge.runtime.agent_loop import Runtime, claim_next
from forge.runtime.worker import Worker
from forge.tools.demo import MetricsReadTool
from tests.integration.conftest import UowFactory

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

INSTRUCTIONS = (
    "You investigate production incidents for the checkout platform. Work from evidence: "
    "query metrics before forming a hypothesis, compare the period before and after any "
    "deployment, and say plainly when the data is insufficient. " * 6
)


async def run_mission(uow: UowFactory, rt: Runtime, agent_id: UUID) -> list[JSONObject]:
    mission = await submit_mission(
        uow,
        SystemClock(),
        MissionRequest(
            objective="Investigate why checkout latency increased after yesterday's deployment.",
            agent_id=agent_id,
            budget=Budget(1_000_000),
        ),
        correlation_id="cache-demo",
    )
    claim = await claim_next(rt)
    assert claim is not None
    final = await Worker(rt, poll_interval_seconds=0.01).run_claim(claim)
    assert final is not None
    assert final.status is ExecutionStatus.COMPLETED
    async with uow() as tx:
        events = await tx.events.list_for_mission(mission.id)
    return [e.payload for e in events if e.event_type == "model.called"]


async def test_repeated_mission_reuses_the_cached_prefix(uow: UowFactory) -> None:
    clock = SystemClock()
    version = await register_agent(
        uow,
        clock,
        name="cached-investigator",
        role="incident investigator",
        instructions=INSTRUCTIONS,
        model=DEMO_MODEL,
        tools=["metrics.read"],
        correlation_id=None,
    )
    metrics = MetricsReadTool()
    rt = Runtime(
        uow_factory=uow,
        clock=clock,
        models={"demo": DemoInvestigationModel()},
        profiles=PROFILES,
        tools={"metrics.read": metrics},
        worker_id="w",
        lease_seconds=30,
    )

    first = await run_mission(uow, rt, version.agent_id)
    second = await run_mission(uow, rt, version.agent_id)

    def usage(call: JSONObject, field: str) -> int:
        value = call["usage"]
        assert isinstance(value, dict)
        result = value[field]
        assert isinstance(result, int)
        return result

    # Mission 1, turn 0: cold cache, so the stable prefix is written.
    assert usage(first[0], "cache_creation_input_tokens") > 0
    assert usage(first[0], "cache_read_input_tokens") == 0
    # Mission 1, turn 1: the prefix and the transcript so far are read back.
    assert usage(first[1], "cache_read_input_tokens") > 0
    # Mission 2, turn 0: a new mission reuses the agent-scoped prefix of mission 1.
    assert usage(second[0], "cache_read_input_tokens") > 0
    assert all(isinstance(c["cache_savings_micro_usd"], int) for c in first + second)
    assert sum(c["cache_savings_micro_usd"] for c in second) > 0  # type: ignore[misc]
    # Tools + platform preamble alone are below the demo model's 256-token minimum, so the
    # planner places no breakpoint there; a provider would ignore one anyway.
    assert first[0]["cache_breakpoints"] == ["agent.identity", "policies.mission", "transcript"]


async def test_routing_falls_back_to_an_available_model_and_records_why(uow: UowFactory) -> None:
    clock = SystemClock()
    version = await register_agent(
        uow,
        clock,
        name="fallback",
        role="investigator",
        instructions=INSTRUCTIONS,
        model=ModelRef("anthropic", "claude-sonnet-5-5"),
        fallback_models=[DEMO_MODEL],
        tools=["metrics.read"],
        correlation_id=None,
    )
    rt = Runtime(
        uow_factory=uow,
        clock=clock,
        models={"demo": DemoInvestigationModel()},
        profiles=PROFILES,
        tools={"metrics.read": MetricsReadTool()},
        worker_id="w",
        lease_seconds=30,
    )
    mission = await submit_mission(
        uow,
        clock,
        MissionRequest(
            objective="Why is checkout slow?", agent_id=version.agent_id, budget=Budget(10**6)
        ),
        correlation_id="routing",
    )

    claim = await claim_next(rt)

    assert claim is not None
    assert claim.execution.model == DEMO_MODEL
    async with uow() as tx:
        (routed,) = [
            e
            for e in await tx.events.list_for_mission(mission.id)
            if e.event_type == "model.routed"
        ]
    assert routed.payload["selected"] == "demo/demo-investigator-1"
    assert routed.payload["candidates"] == [
        "anthropic/claude-sonnet-5-5: provider 'anthropic' is not configured",
        "demo/demo-investigator-1: selected",
    ]


async def test_context_that_cannot_fit_any_candidate_is_rejected_at_routing(
    uow: UowFactory,
) -> None:
    clock = SystemClock()
    version = await register_agent(
        uow,
        clock,
        name="verbose",
        role="investigator",
        instructions="x" * 140_000,
        model=DEMO_MODEL,
        tools=["metrics.read"],
        correlation_id=None,
    )
    mission = await submit_mission(
        uow,
        clock,
        MissionRequest(objective="o", agent_id=version.agent_id, budget=Budget(10**6)),
        correlation_id="overflow",
    )
    rt = Runtime(
        uow_factory=uow,
        clock=clock,
        models={"demo": DemoInvestigationModel()},
        profiles=PROFILES,
        tools={"metrics.read": MetricsReadTool()},
        worker_id="w",
        lease_seconds=30,
    )

    assert await claim_next(rt) is None  # nothing to run: the execution failed at routing

    async with uow() as tx:
        (execution,) = await tx.executions.list_for_mission(mission.id)
    assert execution.failure_kind is FailureKind.NO_ELIGIBLE_MODEL
    assert execution.failure_detail is not None
    assert "window is 32000" in execution.failure_detail
