"""Policy evaluation, approval binding, and the governance stage of the pipeline (ADR 0010)."""

from collections.abc import Mapping
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest

from forge.domain.common import DomainError, JSONValue
from forge.domain.governance import (
    Approval,
    ApprovalStatus,
    Condition,
    Effect,
    Operator,
    Policy,
    Rule,
    evaluate,
)
from forge.domain.mission import RiskPolicy
from forge.domain.risk import RiskLevel
from forge.domain.tool import ToolResult, arguments_sha256
from forge.tools.base import ToolContext
from forge.tools.pipeline import Decision, Governance, Stage, authorize
from tests import factories

NOW = factories.NOW
KEY = "execution:0:call-1"
ARGS: dict[str, JSONValue] = {"service": "checkout", "replicas": 3}


class StubTool:
    def __init__(self, risk: RiskLevel, name: str = "deploy.scale") -> None:
        self.definition = factories.tool_definition(name=name, risk=risk)

    async def invoke(self, arguments: Mapping[str, JSONValue], context: ToolContext) -> ToolResult:
        return ToolResult("ok")


def _policy(*rules: Rule, name: str = "ops", version: int = 1) -> Policy:
    return Policy(name, version, rules, "alice", NOW)


def _approval(**overrides: object) -> Approval:
    base = Approval(
        id=uuid4(),
        mission_id=uuid4(),
        task_id=uuid4(),
        execution_id=uuid4(),
        idempotency_key=KEY,
        agent_id=uuid4(),
        tool="deploy.scale",
        arguments=ARGS,
        arguments_sha256=arguments_sha256(ARGS),
        rationale="scale out to absorb load",
        evidence=("call-0",),
        affected_resources=("service=checkout",),
        risk="HIGH",
        status=ApprovalStatus.PENDING,
        expires_at=NOW + timedelta(hours=1),
        requested_at=NOW,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def _decide(
    risk: RiskLevel,
    governance: Governance,
    arguments: Mapping[str, JSONValue] | None = ARGS,
    agent_name: str = "investigator",
) -> Decision:
    agent, version = factories.agent_and_version(tools=("deploy.scale",))
    mission = factories.mission(risk_policy=RiskPolicy(RiskLevel.CRITICAL))
    task = factories.task(mission_id=mission.id, agent_id=agent.id, allowed_tools=("deploy.scale",))
    return authorize(
        "deploy.scale",
        tools={"deploy.scale": StubTool(risk)},
        version=version,
        task=task,
        mission=mission,
        agent_name=agent_name,
        arguments=arguments,
        governance=governance,
    )


# --- Policy evaluation ---------------------------------------------------------------


def test_no_matching_rule_denies() -> None:
    decision = evaluate([], tool="deploy.scale", agent_name="a", arguments=ARGS)
    assert (decision.effect, decision.policy) == (Effect.DENY, None)


def test_first_matching_rule_wins_in_policy_name_order() -> None:
    allow = _policy(Rule(Effect.ALLOW, ("deploy.*",)), name="b-allow")
    deny = _policy(Rule(Effect.DENY, ("deploy.scale",), reason="frozen"), name="a-deny")

    decision = evaluate([allow, deny], tool="deploy.scale", agent_name="a", arguments=ARGS)

    assert (decision.effect, decision.policy) == (Effect.DENY, "a-deny@1")


def test_rules_can_be_scoped_to_agents() -> None:
    policy = _policy(Rule(Effect.ALLOW, ("deploy.*",), agents=("operator-bot",)))

    assert evaluate([policy], tool="deploy.scale", agent_name="other", arguments=ARGS).effect is (
        Effect.DENY
    )
    assert (
        evaluate([policy], tool="deploy.scale", agent_name="operator-bot", arguments=ARGS).effect
        is Effect.ALLOW
    )


@pytest.mark.parametrize(
    ("condition", "holds"),
    [
        (Condition("service", Operator.EQ, "checkout"), True),
        (Condition("service", Operator.NE, "checkout"), False),
        (Condition("service", Operator.IN, ["checkout", "payments"]), True),
        (Condition("service", Operator.NOT_IN, ["checkout"]), False),
        (Condition("service", Operator.PREFIX, "check"), True),
        (Condition("service", Operator.MAX_LENGTH, 4), False),
        (Condition("missing", Operator.EQ, "x"), False),
    ],
)
def test_conditions(condition: Condition, holds: bool) -> None:
    assert condition.holds(ARGS) is holds


def test_conditional_rule_falls_through_when_its_condition_fails() -> None:
    policy = _policy(
        Rule(Effect.ALLOW, ("deploy.*",), conditions=(Condition("service", Operator.EQ, "x"),)),
        Rule(Effect.DENY, ("deploy.*",), reason="only x"),
    )

    assert evaluate([policy], tool="deploy.scale", agent_name="a", arguments=ARGS).reason == (
        "only x"
    )


def test_could_pass_check_ignores_conditional_denials_but_honours_unconditional_ones() -> None:
    conditional = _policy(
        Rule(Effect.DENY, ("deploy.*",), conditions=(Condition("service", Operator.EQ, "x"),)),
        Rule(Effect.ALLOW, ("deploy.*",)),
    )
    absolute = _policy(Rule(Effect.DENY, ("deploy.*",)))

    assert evaluate([conditional], tool="deploy.scale", agent_name="a", arguments=None).effect is (
        Effect.ALLOW
    )
    assert evaluate([absolute], tool="deploy.scale", agent_name="a", arguments=None).effect is (
        Effect.DENY
    )


def test_policies_need_a_name_and_positive_version() -> None:
    with pytest.raises(DomainError):
        Policy("", 1, (), "alice", NOW)
    with pytest.raises(DomainError):
        Policy("ops", 0, (), "alice", NOW)


# --- Approvals -------------------------------------------------------------------------


def test_an_approval_authorizes_only_the_exact_call_before_expiry() -> None:
    approved = _approval().decide(ApprovalStatus.APPROVED, "alice", "ok", NOW)
    digest = arguments_sha256(ARGS)

    assert approved.authorizes(KEY, digest, NOW)
    assert not approved.authorizes("execution:0:call-2", digest, NOW)
    assert not approved.authorizes(KEY, arguments_sha256({**ARGS, "replicas": 300}), NOW)
    assert not approved.authorizes(KEY, digest, NOW + timedelta(hours=2))
    denied = _approval().decide(ApprovalStatus.DENIED, "alice", "no", NOW)
    assert not denied.authorizes(KEY, digest, NOW)


def test_a_decided_approval_cannot_be_decided_again() -> None:
    approved = _approval().decide(ApprovalStatus.APPROVED, "alice", "ok", NOW)
    with pytest.raises(DomainError):
        approved.decide(ApprovalStatus.DENIED, "mallory", "changed my mind", NOW)


# --- Governance stage --------------------------------------------------------------------


def test_medium_runs_only_when_a_policy_allows_it() -> None:
    allow = _policy(Rule(Effect.ALLOW, ("deploy.*",)))

    assert not _decide(RiskLevel.MEDIUM, Governance()).allowed
    assert _decide(RiskLevel.MEDIUM, Governance(policies=(allow,))).allowed


def test_medium_can_be_routed_to_a_human_by_policy() -> None:
    gate = _policy(Rule(Effect.REQUIRE_APPROVAL, ("deploy.*",)))

    decision = _decide(RiskLevel.MEDIUM, Governance(policies=(gate,), idempotency_key=KEY, now=NOW))

    assert (decision.allowed, decision.requires_approval) == (False, True)


def test_high_is_never_auto_allowed_by_policy() -> None:
    allow = _policy(Rule(Effect.ALLOW, ("deploy.*",)))

    decision = _decide(RiskLevel.HIGH, Governance(policies=(allow,), idempotency_key=KEY, now=NOW))

    assert (decision.allowed, decision.stage, decision.requires_approval) == (
        False,
        Stage.APPROVAL,
        True,
    )


def test_high_can_be_denied_outright_by_an_explicit_rule() -> None:
    deny = _policy(Rule(Effect.DENY, ("deploy.*",), reason="change freeze"))

    decision = _decide(RiskLevel.HIGH, Governance(policies=(deny,), idempotency_key=KEY, now=NOW))

    assert (decision.allowed, decision.stage, decision.requires_approval) == (
        False,
        Stage.POLICY,
        False,
    )
    assert "change freeze" in decision.reason


def test_high_runs_with_an_approval_of_this_exact_call() -> None:
    approved = _approval().decide(ApprovalStatus.APPROVED, "alice", "ok", NOW)

    decision = _decide(RiskLevel.HIGH, Governance(approval=approved, idempotency_key=KEY, now=NOW))

    assert decision.allowed


def test_an_approval_does_not_carry_over_to_different_arguments() -> None:
    approved = _approval().decide(ApprovalStatus.APPROVED, "alice", "ok", NOW)

    decision = _decide(
        RiskLevel.HIGH,
        Governance(approval=approved, idempotency_key=KEY, now=NOW),
        arguments={**ARGS, "replicas": 300},
    )

    assert (decision.allowed, decision.requires_approval) == (False, True)


@pytest.mark.parametrize(
    ("status", "at", "fragment"),
    [
        (ApprovalStatus.DENIED, NOW, "denied by alice"),
        (ApprovalStatus.EXPIRED, NOW, "expired by alice"),
        (ApprovalStatus.APPROVED, NOW + timedelta(hours=2), "expired before execution"),
    ],
)
def test_denied_or_expired_approvals_become_denials(
    status: ApprovalStatus, at: object, fragment: str
) -> None:
    closed = _approval().decide(status, "alice", "reason", NOW)

    decision = _decide(
        RiskLevel.HIGH,
        Governance(approval=closed, idempotency_key=KEY, now=at),  # type: ignore[arg-type]
    )

    assert (decision.allowed, decision.requires_approval) == (False, False)
    assert fragment in decision.reason


def test_a_pending_approval_keeps_the_call_waiting() -> None:
    decision = _decide(
        RiskLevel.HIGH, Governance(approval=_approval(), idempotency_key=KEY, now=NOW)
    )

    assert (decision.allowed, decision.requires_approval) == (False, True)
    assert "awaiting" in decision.reason


def test_critical_is_disabled_unless_enabled_and_then_still_needs_approval() -> None:
    disabled = _decide(RiskLevel.CRITICAL, Governance(idempotency_key=KEY, now=NOW))
    enabled = _decide(
        RiskLevel.CRITICAL,
        Governance(critical_enabled=frozenset({"deploy.scale"}), idempotency_key=KEY, now=NOW),
    )

    assert (disabled.allowed, disabled.stage, disabled.requires_approval) == (
        False,
        Stage.POLICY,
        False,
    )
    assert (enabled.allowed, enabled.requires_approval) == (False, True)


def test_exposure_offers_tools_that_could_pass_with_approval() -> None:
    could = _decide(RiskLevel.HIGH, Governance(), arguments=None)
    medium_without_policy = _decide(RiskLevel.MEDIUM, Governance(), arguments=None)

    assert (could.allowed, could.requires_approval) == (True, True)
    assert not medium_without_policy.allowed
