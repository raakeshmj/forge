"""Retries, timeouts, budgets, deadlines, step limits, cancellation, and tool denial."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from forge.domain.common import JSONObject
from forge.domain.execution import Execution, ExecutionStatus, FailureKind
from forge.domain.mission import MissionStatus
from forge.domain.model import ModelRequest, ModelResponse, ToolResultBlock
from forge.domain.risk import RiskLevel
from forge.domain.task import TaskStatus
from forge.domain.tool import Idempotency, InvocationStatus, idempotency_key
from forge.inference.client import ModelCallError
from forge.infrastructure.clock import SystemClock
from forge.orchestration.missions import cancel_mission
from forge.runtime.agent_loop import Runtime, claim_next
from forge.runtime.worker import Worker
from tests.integration.conftest import UowFactory
from tests.runtime_doubles import (
    CALL_COST,
    FAST_POLICY,
    RecordingTool,
    ScriptedModel,
    answer,
    hang,
    runtime,
    submit,
    tool_call,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def run_next(rt: Runtime) -> Execution:
    claim = await claim_next(rt)
    assert claim is not None
    final = await Worker(rt, poll_interval_seconds=0.01).run_claim(claim)
    assert final is not None
    return final


async def event_types(uow: UowFactory, mission_id: UUID) -> list[str]:
    async with uow() as tx:
        return [e.event_type for e in await tx.events.list_for_mission(mission_id)]


async def payloads(uow: UowFactory, mission_id: UUID, event_type: str) -> list[JSONObject]:
    async with uow() as tx:
        events = await tx.events.list_for_mission(mission_id)
    return [e.payload for e in events if e.event_type == event_type]


def tool_results(request: ModelRequest) -> list[ToolResultBlock]:
    return [b for m in request.messages for b in m.content if isinstance(b, ToolResultBlock)]


# --- Model retries and timeouts ----------------------------------------------------------


async def test_transient_model_errors_are_retried(uow: UowFactory) -> None:
    model = ScriptedModel(
        ModelCallError("overloaded", retryable=True),
        ModelCallError("overloaded", retryable=True),
        tool_call(),
        answer(),
    )
    mission_id = await submit(uow)

    final = await run_next(runtime(uow, model, RecordingTool()))

    assert final.status is ExecutionStatus.COMPLETED
    failed = await payloads(uow, mission_id, "model.failed")
    assert [(p["attempt"], p["retryable"]) for p in failed] == [(1, True), (2, True)]
    assert [p["attempts"] for p in await payloads(uow, mission_id, "model.called")] == [3, 1]


async def test_permanent_model_error_fails_without_retry(uow: UowFactory) -> None:
    model = ScriptedModel(ModelCallError("invalid request", retryable=False))
    mission_id = await submit(uow)

    final = await run_next(runtime(uow, model, RecordingTool()))

    assert final.status is ExecutionStatus.FAILED
    assert final.failure_kind is FailureKind.MODEL_FAILED
    assert len(model.requests) == 1
    async with uow() as tx:
        assert (await tx.missions.get(mission_id)).status is MissionStatus.FAILED


async def test_model_timeouts_are_retried_then_fail(uow: UowFactory) -> None:
    policy = replace(FAST_POLICY, model_timeout_seconds=0.05, model_max_attempts=2)
    model = ScriptedModel(hang, hang)
    await submit(uow, policy=policy)

    final = await run_next(runtime(uow, model, RecordingTool()))

    assert final.status is ExecutionStatus.FAILED
    assert final.failure_kind is FailureKind.MODEL_FAILED
    assert final.failure_detail == "TimeoutError after 2 attempt(s)"
    assert final.cost_micro_usd == 0


async def test_model_without_a_profile_is_never_called(uow: UowFactory) -> None:
    model = ScriptedModel(answer())
    mission_id = await submit(uow)

    assert await claim_next(replace(runtime(uow, model, RecordingTool()), profiles={})) is None

    assert model.requests == []
    async with uow() as tx:
        (execution,) = await tx.executions.list_for_mission(mission_id)
        mission = await tx.missions.get(mission_id)
    assert execution.failure_kind is FailureKind.NO_ELIGIBLE_MODEL
    assert mission.status is MissionStatus.FAILED


# --- Tool retries and timeouts -----------------------------------------------------------


async def test_read_only_tool_failures_are_retried(uow: UowFactory) -> None:
    tool = RecordingTool(max_attempts=3, behaviours=["fail_retryable", "fail_retryable"])
    mission_id = await submit(uow)

    final = await run_next(runtime(uow, ScriptedModel(tool_call(), answer()), tool))

    assert final.status is ExecutionStatus.COMPLETED
    assert len(tool.invocations) == 3
    assert [p["attempts"] for p in await payloads(uow, mission_id, "tool.completed")] == [3]


async def test_non_idempotent_tool_failure_is_not_retried_and_goes_back_to_the_model(
    uow: UowFactory,
) -> None:
    model = ScriptedModel(tool_call(), answer("could not apply the change"))
    tool = RecordingTool(idempotency=Idempotency.NON_IDEMPOTENT, behaviours=["fail_retryable"])
    mission_id = await submit(uow)

    final = await run_next(runtime(uow, model, tool))

    assert final.status is ExecutionStatus.COMPLETED
    assert len(tool.invocations) == 1
    (result,) = tool_results(model.requests[1])
    assert result.is_error
    assert [p["attempts"] for p in await payloads(uow, mission_id, "tool.failed")] == [1]
    async with uow() as tx:
        ledger = await tx.tool_invocations.get(idempotency_key(final.id, 0, "c1"))
    assert ledger is not None
    assert ledger.status is InvocationStatus.FAILED


async def test_read_only_tool_timeout_is_retried(uow: UowFactory) -> None:
    tool = RecordingTool(timeout_seconds=0.05, max_attempts=2, behaviours=["hang"])

    await submit(uow)
    final = await run_next(runtime(uow, ScriptedModel(tool_call(), answer()), tool))

    assert final.status is ExecutionStatus.COMPLETED
    assert len(tool.invocations) == 2


async def test_non_idempotent_tool_timeout_is_an_unknown_outcome(uow: UowFactory) -> None:
    tool = RecordingTool(
        idempotency=Idempotency.NON_IDEMPOTENT, timeout_seconds=0.05, behaviours=["hang"]
    )
    await submit(uow)

    final = await run_next(runtime(uow, ScriptedModel(tool_call(), answer()), tool))

    assert final.status is ExecutionStatus.FAILED
    assert final.failure_kind is FailureKind.TOOL_OUTCOME_UNKNOWN
    assert len(tool.invocations) == 1
    async with uow() as tx:
        ledger = await tx.tool_invocations.get(idempotency_key(final.id, 0, "c1"))
    assert ledger is not None
    assert ledger.status is InvocationStatus.INTENDED  # left for a human to reconcile


# --- Budget, deadline, step limit --------------------------------------------------------


async def test_a_call_whose_worst_case_exceeds_the_budget_is_never_made(uow: UowFactory) -> None:
    model = ScriptedModel(tool_call(), answer())
    mission_id = await submit(uow, budget=CALL_COST)  # far below max_output_tokens' worst case

    final = await run_next(runtime(uow, model, RecordingTool()))

    assert final.failure_kind is FailureKind.BUDGET_EXCEEDED
    assert model.requests == []  # refused before calling, so nothing was overspent
    assert final.cost_micro_usd == 0
    failed = await payloads(uow, mission_id, "mission.failed")
    assert failed == [{"failure_kind": "BUDGET_EXCEEDED", "cost_micro_usd": 0}]


async def test_budget_reservation_stops_the_next_call_once_spend_leaves_no_room(
    uow: UowFactory,
) -> None:
    tool = RecordingTool()
    policy = replace(FAST_POLICY, max_output_tokens=50)  # worst case per call is small
    # Room for one call's worst case (~350), not for spend plus a second worst case.
    limit = 450
    await submit(uow, budget=limit, policy=policy)

    final = await run_next(runtime(uow, ScriptedModel(tool_call(), answer()), tool))

    assert final.failure_kind is FailureKind.BUDGET_EXCEEDED
    assert final.cost_micro_usd == CALL_COST + 7  # one call and its tool; never above budget
    assert final.cost_micro_usd <= limit
    assert len(tool.invocations) == 1


async def test_passed_deadline_fails_without_calling_the_model(uow: UowFactory) -> None:
    model = ScriptedModel(answer())
    await submit(uow, deadline=datetime.now(UTC) - timedelta(seconds=1))

    final = await run_next(runtime(uow, model, RecordingTool()))

    assert final.failure_kind is FailureKind.DEADLINE_EXCEEDED
    assert model.requests == []


async def test_step_limit_bounds_model_turns(uow: UowFactory) -> None:
    model = ScriptedModel(tool_call(call_id="c1"), tool_call(call_id="c2"), answer())
    tool = RecordingTool()
    await submit(uow, policy=replace(FAST_POLICY, max_steps=2))

    final = await run_next(runtime(uow, model, tool))

    assert final.failure_kind is FailureKind.MAX_STEPS_EXCEEDED
    assert len(model.requests) == 2
    assert len(tool.invocations) == 2


# --- Cancellation ------------------------------------------------------------------------


async def test_cancellation_stops_a_running_execution_at_the_next_step(uow: UowFactory) -> None:
    mission_id = await submit(uow)

    async def cancel_during_tool() -> None:
        await cancel_mission(uow, SystemClock(), mission_id)

    model = ScriptedModel(tool_call(), answer())
    tool = RecordingTool(on_invoke=cancel_during_tool)

    final = await run_next(runtime(uow, model, tool))

    assert final.status is ExecutionStatus.CANCELLED
    assert len(model.requests) == 1  # no model turn after cancellation
    async with uow() as tx:
        assert (await tx.missions.get(mission_id)).status is MissionStatus.CANCELLED
        (task,) = await tx.tasks.list_for_mission(mission_id)
    assert task.status is TaskStatus.CANCELLED
    types = await event_types(uow, mission_id)
    assert types[-1] == "agent.cancelled"
    assert "mission.completed" not in types


async def test_cancellation_racing_the_final_answer_wins(uow: UowFactory) -> None:
    mission_id = await submit(uow)

    async def cancel_then_answer(_: ModelRequest) -> ModelResponse:
        await cancel_mission(uow, SystemClock(), mission_id)
        return answer("too late")

    final = await run_next(runtime(uow, ScriptedModel(cancel_then_answer), RecordingTool()))

    assert final.status is ExecutionStatus.CANCELLED
    assert final.output is None
    async with uow() as tx:
        assert (await tx.missions.get(mission_id)).status is MissionStatus.CANCELLED
        checkpoints = await tx.checkpoints.list(final.id)
    assert checkpoints[-1].state.turns == 1  # the answer is kept in the trajectory


# --- Tool authorization ------------------------------------------------------------------


async def test_high_risk_tool_is_denied_without_approval_and_never_runs(uow: UowFactory) -> None:
    restart = RecordingTool(name="deployment.restart", risk=RiskLevel.HIGH)
    model = ScriptedModel(tool_call("deployment.restart"), answer("could not restart"))
    mission_id = await submit(uow, tools=("deployment.restart",), max_risk=RiskLevel.HIGH)

    final = await run_next(runtime(uow, model, restart))

    assert final.status is ExecutionStatus.COMPLETED
    assert restart.invocations == []
    assert model.requests[0].tools == ()  # never offered: it could not be authorized
    (result,) = tool_results(model.requests[1])
    assert result.is_error
    denied = await payloads(uow, mission_id, "tool.denied")
    assert [(p["tool"], p["stage"]) for p in denied] == [("deployment.restart", "policy")]


async def test_tool_outside_the_agents_bindings_is_denied(uow: UowFactory) -> None:
    logs = RecordingTool(name="logs.read")
    model = ScriptedModel(tool_call("logs.read"), answer())
    mission_id = await submit(uow, tools=("metrics.read",))

    await run_next(runtime(uow, model, RecordingTool(), logs))

    assert logs.invocations == []
    denied = await payloads(uow, mission_id, "tool.denied")
    assert [p["stage"] for p in denied] == ["permission"]
