"""Durable human approval of high-risk tool calls, policies, and audit (ADR 0010).

Phase 8 definition of done: a HIGH tool call waits durably for a human, survives a
worker restart, runs exactly once after approval, and never runs when denied,
expired, or cancelled.
"""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from forge.domain.execution import Execution, ExecutionStatus
from forge.domain.governance import Approval, ApprovalStatus, Effect, Rule
from forge.domain.mission import Budget, MissionStatus, RiskPolicy
from forge.domain.model import ToolResultBlock
from forge.domain.plan import PlannedTask
from forge.domain.risk import RiskLevel
from forge.domain.task import TaskStatus
from forge.domain.tool import Idempotency
from forge.inference.client import ModelCallError
from forge.infrastructure.clock import SystemClock
from forge.orchestration.agents import register_agent
from forge.orchestration.missions import MissionRequest, cancel_mission, submit_mission
from forge.runtime.agent_loop import Runtime, claim_next
from forge.runtime.worker import Worker
from forge.security import approvals, governance
from tests.integration.conftest import UowFactory
from tests.runtime_doubles import (
    FAST_POLICY,
    TEST_MODEL,
    RecordingTool,
    ScriptedModel,
    answer,
    runtime,
    submit,
    tool_call,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

CLOCK = SystemClock()


def restart_tool() -> RecordingTool:
    return RecordingTool(
        name="deployment.restart", risk=RiskLevel.HIGH, idempotency=Idempotency.NON_IDEMPOTENT
    )


async def run_next(rt: Runtime) -> Execution | None:
    claim = await claim_next(rt)
    if claim is None:
        return None
    return await Worker(rt, poll_interval_seconds=0.01).run_claim(claim)


async def next_status(rt: Runtime) -> ExecutionStatus | None:
    final = await run_next(rt)
    return None if final is None else final.status


async def submit_high(uow: UowFactory) -> UUID:
    return await submit(uow, tools=("deployment.restart",), max_risk=RiskLevel.HIGH)


async def the_approval(uow: UowFactory, mission_id: UUID) -> Approval:
    async with uow() as tx:
        (approval,) = [a for a in await tx.approvals.list_approvals() if a.mission_id == mission_id]
    return approval


async def event_types(uow: UowFactory, mission_id: UUID) -> list[str]:
    async with uow() as tx:
        return [e.event_type for e in await tx.events.list_for_mission(mission_id)]


async def audit_decisions(uow: UowFactory, mission_id: UUID) -> list[tuple[str, str]]:
    async with uow() as tx:
        records = await tx.audit.list_records(mission_id=mission_id)
    return [(r.action, r.decision) for r in reversed(records)]


async def test_high_risk_call_waits_durably_survives_restart_and_runs_once_after_approval(
    uow: UowFactory,
) -> None:
    mission_id = await submit_high(uow)
    first_tool = restart_tool()
    first_model = ScriptedModel(tool_call("deployment.restart", service="checkout"))

    parked = await run_next(runtime(uow, first_model, first_tool, worker_id="w1"))

    assert parked is not None
    assert parked.status is ExecutionStatus.WAITING_APPROVAL
    assert first_tool.invocations == []
    assert [t.name for t in first_model.requests[0].tools] == ["deployment.restart"]
    approval = await the_approval(uow, mission_id)
    assert approval.status is ApprovalStatus.PENDING
    assert (approval.tool, approval.risk, approval.rationale) == (
        "deployment.restart",
        "HIGH",
        "checking",
    )
    assert approval.affected_resources == ("service=checkout",)
    assert approval.arguments == {"service": "checkout"}
    async with uow() as tx:
        mission = await tx.missions.get(mission_id)
        (task,) = await tx.tasks.list_for_mission(mission_id)
    assert (mission.status, task.status) == (MissionStatus.WAITING_APPROVAL, TaskStatus.BLOCKED)

    # The worker process is gone; a new one finds nothing to do while the human decides.
    tool, model = restart_tool(), ScriptedModel(answer("restarted checkout"))
    replacement = runtime(uow, model, tool, worker_id="w2")
    assert await claim_next(replacement) is None

    await approvals.decide(
        uow, CLOCK, approval.id, approve=True, operator="alice", reason="change approved"
    )
    final = await run_next(replacement)

    assert final is not None
    assert final.status is ExecutionStatus.COMPLETED
    assert final.attempt == parked.attempt  # resuming after a decision is not a recovery
    assert len(tool.invocations) == 1
    assert sum(tool.effects.values()) == 1
    assert await run_next(replacement) is None  # nothing left: the side effect ran once
    async with uow() as tx:
        assert (await tx.missions.get(mission_id)).status is MissionStatus.COMPLETED
    types = await event_types(uow, mission_id)
    assert types.index("approval.requested") < types.index("approval.completed")
    assert types.index("approval.completed") < types.index("tool.called")
    assert await audit_decisions(uow, mission_id) == [
        ("tool.execute", "approval_requested"),
        ("approval.decide", "approved"),
        ("tool.execute", "allowed"),
    ]


async def test_a_denied_call_never_runs_and_the_model_learns_why(uow: UowFactory) -> None:
    mission_id = await submit_high(uow)
    tool = restart_tool()
    model = ScriptedModel(tool_call("deployment.restart"), answer("restart was refused"))
    rt = runtime(uow, model, tool)
    await run_next(rt)
    approval = await the_approval(uow, mission_id)

    await approvals.decide(uow, CLOCK, approval.id, approve=False, operator="bob", reason="freeze")
    final = await run_next(rt)

    assert final is not None
    assert final.status is ExecutionStatus.COMPLETED
    assert tool.invocations == []
    (result,) = [
        b
        for m in model.requests[1].messages
        for b in m.content
        if isinstance(b, ToolResultBlock) and b.is_error
    ]
    assert "denied by bob: freeze" in result.content
    assert ("tool.execute", "denied:approval") in await audit_decisions(uow, mission_id)


async def test_an_undecided_request_expires_and_the_call_never_runs(uow: UowFactory) -> None:
    mission_id = await submit_high(uow)
    tool = restart_tool()
    model = ScriptedModel(tool_call("deployment.restart"), answer("nobody answered"))
    rt = runtime(uow, model, tool)
    await run_next(rt)
    approval = await the_approval(uow, mission_id)
    await _age(uow, approval.id)

    final = await run_next(rt)

    assert final is not None
    assert final.status is ExecutionStatus.COMPLETED
    assert tool.invocations == []
    assert (await the_approval(uow, mission_id)).status is ApprovalStatus.EXPIRED
    with pytest.raises(approvals.ApprovalError):  # too late to approve
        await approvals.decide(uow, CLOCK, approval.id, approve=True, operator="a", reason="")


async def test_a_late_approval_is_refused_and_recorded_as_expiry(uow: UowFactory) -> None:
    mission_id = await submit_high(uow)
    await run_next(runtime(uow, ScriptedModel(tool_call("deployment.restart")), restart_tool()))
    approval = await the_approval(uow, mission_id)
    await _age(uow, approval.id)

    with pytest.raises(approvals.ApprovalError, match="expired"):
        await approvals.decide(uow, CLOCK, approval.id, approve=True, operator="a", reason="")

    assert (await the_approval(uow, mission_id)).status is ApprovalStatus.EXPIRED


async def test_a_decision_cannot_be_made_twice(uow: UowFactory) -> None:
    mission_id = await submit_high(uow)
    await run_next(runtime(uow, ScriptedModel(tool_call("deployment.restart")), restart_tool()))
    approval = await the_approval(uow, mission_id)
    await approvals.decide(uow, CLOCK, approval.id, approve=False, operator="a", reason="no")

    with pytest.raises(approvals.ApprovalError, match="already"):
        await approvals.decide(uow, CLOCK, approval.id, approve=True, operator="b", reason="yes")


async def test_cancelling_a_waiting_mission_withdraws_the_request(uow: UowFactory) -> None:
    mission_id = await submit_high(uow)
    tool = restart_tool()
    rt = runtime(uow, ScriptedModel(tool_call("deployment.restart")), tool)
    await run_next(rt)

    await cancel_mission(uow, CLOCK, mission_id)
    final = await run_next(rt)

    assert final is not None
    assert final.status is ExecutionStatus.CANCELLED
    assert tool.invocations == []
    approval = await the_approval(uow, mission_id)
    assert approval.status is ApprovalStatus.EXPIRED
    with pytest.raises(approvals.ApprovalError):
        await approvals.decide(uow, CLOCK, approval.id, approve=True, operator="a", reason="")


async def test_an_explicit_deny_rule_blocks_a_high_risk_call_without_asking(
    uow: UowFactory,
) -> None:
    await governance.write_policy(
        uow,
        CLOCK,
        name="change-freeze",
        rules=(Rule(Effect.DENY, ("deployment.*",), reason="change freeze"),),
        operator="alice",
    )
    mission_id = await submit_high(uow)
    tool = restart_tool()
    model = ScriptedModel(tool_call("deployment.restart"), answer("frozen"))

    final = await run_next(runtime(uow, model, tool))

    assert final is not None
    assert final.status is ExecutionStatus.COMPLETED
    assert tool.invocations == []
    assert model.requests[0].tools == ()  # never offered
    async with uow() as tx:
        assert [a for a in await tx.approvals.list_approvals() if a.mission_id == mission_id] == []
    assert ("tool.execute", "denied:policy") in await audit_decisions(uow, mission_id)


async def test_a_medium_risk_tool_runs_when_a_policy_allows_it(uow: UowFactory) -> None:
    await governance.write_policy(
        uow,
        CLOCK,
        name="tickets",
        rules=(Rule(Effect.ALLOW, ("ticket.*",)),),
        operator="alice",
    )
    comment = RecordingTool(name="ticket.comment", risk=RiskLevel.MEDIUM)
    model = ScriptedModel(tool_call("ticket.comment"), answer("commented"))
    mission_id = await submit(uow, tools=("ticket.comment",), max_risk=RiskLevel.MEDIUM)

    final = await run_next(runtime(uow, model, comment))

    assert final is not None
    assert final.status is ExecutionStatus.COMPLETED
    assert len(comment.invocations) == 1
    assert ("tool.execute", "allowed") in await audit_decisions(uow, mission_id)


async def test_critical_tools_stay_disabled_until_an_operator_enables_them(
    uow: UowFactory,
) -> None:
    drop = RecordingTool(name="database.drop", risk=RiskLevel.CRITICAL)
    mission_id = await submit(uow, tools=("database.drop",), max_risk=RiskLevel.CRITICAL)
    model = ScriptedModel(tool_call("database.drop"), answer("refused"))

    await run_next(runtime(uow, model, drop))

    assert drop.invocations == []
    assert ("tool.execute", "denied:policy") in await audit_decisions(uow, mission_id)

    await governance.set_critical(
        uow, CLOCK, tool="database.drop", enabled=True, operator="root", reason="migration window"
    )
    mission_id = await submit(uow, tools=("database.drop",), max_risk=RiskLevel.CRITICAL)
    parked = await run_next(runtime(uow, ScriptedModel(tool_call("database.drop")), drop))

    assert parked is not None
    assert parked.status is ExecutionStatus.WAITING_APPROVAL
    assert drop.invocations == []  # enabled still means a human approves each call
    assert (await the_approval(uow, mission_id)).risk == "CRITICAL"


async def test_audit_and_policy_history_are_append_only(uow: UowFactory) -> None:
    await governance.write_policy(
        uow, CLOCK, name="p", rules=(Rule(Effect.ALLOW, ("x",)),), operator="alice"
    )
    for statement in (
        "UPDATE audit_records SET decision = 'forged'",
        "DELETE FROM audit_records",
        "UPDATE policies SET created_by = 'mallory'",
        "DELETE FROM policies",
    ):
        with pytest.raises(Exception, match=r"append-only|immutable"):
            async with uow.engine.begin() as connection:
                await connection.execute(text(statement))


async def _age(uow: UowFactory, approval_id: UUID) -> None:
    """Fast-forward past the approval's expiry instead of waiting for it."""
    async with uow.engine.begin() as connection:
        await connection.execute(
            text("UPDATE approvals SET expires_at = now() - interval '1s' WHERE id = :id"),
            {"id": approval_id},
        )


async def test_sibling_tasks_keep_running_while_one_waits_for_a_human(uow: UowFactory) -> None:
    async def agent(tools: tuple[str, ...]) -> UUID:
        version = await register_agent(
            uow,
            CLOCK,
            name=f"agent-{uuid4().hex[:8]}",
            role="worker",
            instructions="Do the task.",
            model=TEST_MODEL,
            tools=tools,
            correlation_id=None,
        )
        return version.agent_id

    operator_agent, reader = await agent(("deployment.restart",)), await agent(("metrics.read",))
    request = MissionRequest(
        objective="Mitigate checkout",
        budget=Budget(10**7),
        risk_policy=RiskPolicy(RiskLevel.HIGH),
        execution_policy=FAST_POLICY,
        tasks=(
            PlannedTask("restart", "Restart checkout", agent_id=operator_agent),
            PlannedTask("watch", "Watch metrics", agent_id=reader),
            PlannedTask("report", "Report", depends_on=("restart", "watch"), agent_id=reader),
        ),
    )
    mission = await submit_mission(uow, CLOCK, request, correlation_id="sibling")
    tool = restart_tool()
    model = ScriptedModel(
        tool_call("deployment.restart"), answer("watched"), answer("restarted"), answer("report")
    )
    rt = runtime(uow, model, tool, RecordingTool())

    assert await next_status(rt) is ExecutionStatus.WAITING_APPROVAL
    assert await next_status(rt) is ExecutionStatus.COMPLETED
    assert await run_next(rt) is None  # "report" waits for both
    async with uow() as tx:
        assert (await tx.missions.get(mission.id)).status is MissionStatus.WAITING_APPROVAL

    approval = await the_approval(uow, mission.id)
    await approvals.decide(uow, CLOCK, approval.id, approve=True, operator="a", reason="ok")
    assert await next_status(rt) is ExecutionStatus.COMPLETED
    assert await next_status(rt) is ExecutionStatus.COMPLETED

    assert len(tool.invocations) == 1
    async with uow() as tx:
        assert (await tx.missions.get(mission.id)).status is MissionStatus.COMPLETED


async def test_a_failing_sibling_ends_the_mission_and_withdraws_the_waiting_request(
    uow: UowFactory,
) -> None:
    async def agent(tools: tuple[str, ...]) -> UUID:
        version = await register_agent(
            uow,
            CLOCK,
            name=f"agent-{uuid4().hex[:8]}",
            role="worker",
            instructions="Do the task.",
            model=TEST_MODEL,
            tools=tools,
            correlation_id=None,
        )
        return version.agent_id

    request = MissionRequest(
        objective="Mitigate checkout",
        budget=Budget(10**7),
        risk_policy=RiskPolicy(RiskLevel.HIGH),
        execution_policy=FAST_POLICY,
        tasks=(
            PlannedTask("restart", "Restart", agent_id=await agent(("deployment.restart",))),
            PlannedTask("watch", "Watch", agent_id=await agent(("metrics.read",))),
        ),
    )
    mission = await submit_mission(uow, CLOCK, request, correlation_id="sibling-fails")
    tool = restart_tool()
    model = ScriptedModel(
        tool_call("deployment.restart"), ModelCallError("invalid request", retryable=False)
    )
    rt = runtime(uow, model, tool, RecordingTool())

    assert await next_status(rt) is ExecutionStatus.WAITING_APPROVAL
    assert await next_status(rt) is ExecutionStatus.FAILED
    assert await next_status(rt) is ExecutionStatus.CANCELLED  # the parked one, released

    assert tool.invocations == []
    assert (await the_approval(uow, mission.id)).status is ApprovalStatus.EXPIRED
    async with uow() as tx:
        assert (await tx.missions.get(mission.id)).status is MissionStatus.FAILED
