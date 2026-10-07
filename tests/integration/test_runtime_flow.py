"""Phase 1 flow: Mission -> Agent -> Model -> Tool -> Model -> Result, fully recorded."""

import pytest

from forge.domain.execution import Execution, ExecutionStatus
from forge.domain.mission import Budget, MissionStatus
from forge.domain.model import ToolResultBlock
from forge.domain.task import TaskStatus
from forge.domain.tool import InvocationStatus, idempotency_key
from forge.inference.demo import DemoInvestigationModel
from forge.inference.pricing import DEMO_MODEL, PRICES
from forge.infrastructure.clock import SystemClock
from forge.orchestration.agents import register_agent
from forge.orchestration.missions import MissionRequest, submit_mission
from forge.runtime.agent_loop import Runtime, claim_next
from forge.runtime.worker import Worker
from forge.tools.demo import MetricsReadTool
from tests.integration.conftest import UowFactory
from tests.runtime_doubles import (
    CALL_COST,
    CALL_USAGE,
    RecordingTool,
    ScriptedModel,
    answer,
    runtime,
    submit,
    tool_call,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def run_next(rt: Runtime) -> Execution:
    claim = await claim_next(rt)
    assert claim is not None, "expected claimable work"
    final = await Worker(rt, poll_interval_seconds=0.01).run_claim(claim)
    assert final is not None
    return final


async def test_mission_agent_model_tool_model_result(uow: UowFactory) -> None:
    model = ScriptedModel(tool_call(service="checkout"), answer("p95 regressed after deploy"))
    tool = RecordingTool()
    mission_id = await submit(uow)

    final = await run_next(runtime(uow, model, tool))

    assert final.status is ExecutionStatus.COMPLETED
    assert final.output == "p95 regressed after deploy"
    assert final.usage == CALL_USAGE + CALL_USAGE
    assert final.cost_micro_usd == 2 * CALL_COST + 7  # two model calls plus the tool's cost
    key = idempotency_key(final.id, 0, "c1")
    assert tool.invocations == [key]
    # The model saw only authorized tools, and the tool result came back as data.
    assert [t.name for t in model.requests[0].tools] == ["metrics.read"]
    results = [
        b for m in model.requests[1].messages for b in m.content if isinstance(b, ToolResultBlock)
    ]
    assert results == [ToolResultBlock("c1", "metrics.read ok {'service': 'checkout'}")]

    async with uow() as tx:
        mission = await tx.missions.get(mission_id)
        (task,) = await tx.tasks.list_for_mission(mission_id)
        checkpoints = await tx.checkpoints.list(final.id)
        events = await tx.events.list_for_mission(mission_id)
        ledger = await tx.tool_invocations.get(key)
    assert mission.status is MissionStatus.COMPLETED
    assert task.status is TaskStatus.COMPLETED
    assert [c.sequence for c in checkpoints] == [1, 2, 3, 4]
    assert checkpoints[-1].state.turns == 2
    assert ledger is not None
    assert (ledger.status, ledger.invocations) == (InvocationStatus.SUCCEEDED, 1)
    assert [e.event_type for e in events] == [
        "mission.created",
        "task.created",
        "mission.planned",
        "task.started",
        "mission.started",
        "agent.started",
        "checkpoint.created",
        "model.called",
        "checkpoint.created",
        "tool.called",
        "tool.completed",
        "checkpoint.created",
        "model.called",
        "checkpoint.created",
        "agent.completed",
        "task.completed",
        "mission.completed",
    ]
    assert {e.correlation_id for e in events} == {"corr-test"}
    assert events[-1].payload == {"cost_micro_usd": 2 * CALL_COST + 7}


async def test_demo_composition_answers_the_checkout_question(uow: UowFactory) -> None:
    """The worker's real wiring: deterministic demo model, metrics tool, published prices."""
    clock = SystemClock()
    version = await register_agent(
        uow,
        clock,
        name="demo-investigator",
        role="investigator",
        instructions="Investigate checkout latency.",
        model=DEMO_MODEL,
        tools=["metrics.read"],
        correlation_id=None,
    )
    await submit_mission(
        uow,
        clock,
        MissionRequest(
            objective="Investigate why checkout latency increased after yesterday's deployment.",
            agent_id=version.agent_id,
            budget=Budget(100_000),
        ),
        correlation_id="demo",
    )
    metrics = MetricsReadTool()
    rt = Runtime(
        uow_factory=uow,
        clock=clock,
        models={DEMO_MODEL.provider: DemoInvestigationModel()},
        prices=PRICES,
        tools={"metrics.read": metrics},
        worker_id="demo",
        lease_seconds=30,
    )

    final = await run_next(rt)

    assert final.status is ExecutionStatus.COMPLETED
    assert final.output is not None
    assert "regressed" in final.output
    assert "14:10=880" in final.output
    assert final.cost_micro_usd > 0
    assert final.usage.input_tokens > 0


async def test_no_work_means_no_claim(uow: UowFactory) -> None:
    assert await claim_next(runtime(uow, ScriptedModel())) is None
