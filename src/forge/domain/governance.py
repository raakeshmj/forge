"""Governance: policies, approvals, and audit records (SECURITY.md, ADR 0010).

Policy evaluation is pure and deterministic over a snapshot: the first matching
rule decides, and no matching rule denies.
"""

import fnmatch
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from forge.domain.common import DomainError, JSONValue, require_transition


class Effect(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class Operator(StrEnum):
    EQ = "eq"
    NE = "ne"
    IN = "in"
    NOT_IN = "not_in"
    PREFIX = "prefix"
    MAX_LENGTH = "max_length"


@dataclass(frozen=True, slots=True)
class Condition:
    argument: str  # top-level argument name
    operator: Operator
    value: JSONValue

    def holds(self, arguments: Mapping[str, JSONValue]) -> bool:
        actual = arguments.get(self.argument)
        match self.operator:
            case Operator.EQ:
                return actual == self.value
            case Operator.NE:
                return actual != self.value
            case Operator.IN:
                return isinstance(self.value, list) and actual in self.value
            case Operator.NOT_IN:
                return isinstance(self.value, list) and actual not in self.value
            case Operator.PREFIX:
                return (
                    isinstance(actual, str)
                    and isinstance(self.value, str)
                    and actual.startswith(self.value)
                )
            case Operator.MAX_LENGTH:
                return isinstance(self.value, int) and len(str(actual or "")) <= self.value


@dataclass(frozen=True, slots=True)
class Rule:
    effect: Effect
    tools: tuple[str, ...]  # glob patterns, e.g. "database.*"
    agents: tuple[str, ...] = ()  # agent names; empty: any agent
    conditions: tuple[Condition, ...] = ()
    reason: str = ""

    def matches_tool(self, tool: str, agent_name: str) -> bool:
        tool_ok = any(fnmatch.fnmatchcase(tool, pattern) for pattern in self.tools)
        return tool_ok and (not self.agents or agent_name in self.agents)


@dataclass(frozen=True, slots=True)
class Policy:
    name: str
    version: int
    rules: tuple[Rule, ...]
    created_by: str
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.name or self.version < 1:
            raise DomainError("a policy needs a name and a positive version")


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    effect: Effect
    policy: str | None  # "name@version" of the deciding rule's policy
    reason: str


def evaluate(
    policies: Sequence[Policy],
    *,
    tool: str,
    agent_name: str,
    arguments: Mapping[str, JSONValue] | None,
) -> PolicyDecision:
    """First matching rule wins, policies in name order. `arguments=None` asks "could it pass?"."""
    for policy in sorted(policies, key=lambda p: p.name):
        for rule in policy.rules:
            if not rule.matches_tool(tool, agent_name):
                continue
            if arguments is None:
                if rule.effect is Effect.DENY and not rule.conditions:
                    return PolicyDecision(
                        Effect.DENY, f"{policy.name}@{policy.version}", rule.reason
                    )
                if rule.effect is not Effect.DENY:
                    return PolicyDecision(
                        rule.effect, f"{policy.name}@{policy.version}", rule.reason
                    )
                continue
            if all(condition.holds(arguments) for condition in rule.conditions):
                return PolicyDecision(rule.effect, f"{policy.name}@{policy.version}", rule.reason)
    return PolicyDecision(Effect.DENY, None, "no policy rule allows this call")


# --- Approvals ---------------------------------------------------------------------------


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    EXPIRED = "EXPIRED"


_A = ApprovalStatus
APPROVAL_TRANSITIONS: dict[ApprovalStatus, frozenset[ApprovalStatus]] = {
    _A.PENDING: frozenset({_A.APPROVED, _A.DENIED, _A.EXPIRED}),
}


@dataclass(frozen=True, slots=True)
class Approval:
    """A human decision about one exact action (CLAUDE.md §21, ADR 0010)."""

    id: UUID
    mission_id: UUID
    task_id: UUID
    execution_id: UUID
    idempotency_key: str
    agent_id: UUID  # the requester
    tool: str
    arguments: Mapping[str, JSONValue]
    arguments_sha256: str
    rationale: str
    evidence: tuple[str, ...]  # tool_use ids of results the agent had seen
    affected_resources: tuple[str, ...]
    risk: str
    status: ApprovalStatus
    expires_at: datetime
    requested_at: datetime
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_reason: str = ""
    row_version: int = 1

    def decide(self, target: ApprovalStatus, by: str, reason: str, now: datetime) -> "Approval":
        require_transition(APPROVAL_TRANSITIONS, "approval", self.id, self.status, target)
        return replace(self, status=target, decided_by=by, decided_at=now, decision_reason=reason)

    def authorizes(self, idempotency_key: str, arguments_sha256: str, now: datetime) -> bool:
        return (
            self.status is ApprovalStatus.APPROVED
            and self.idempotency_key == idempotency_key
            and self.arguments_sha256 == arguments_sha256
            and now < self.expires_at
        )


# --- Audit -------------------------------------------------------------------------------


class ActorType(StrEnum):
    AGENT = "agent"
    OPERATOR = "operator"
    SYSTEM = "system"


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """SECURITY §11: actor, agent, mission, tool, action, target, decision, approval, time."""

    actor: str
    actor_type: ActorType
    action: str  # e.g. "tool.execute", "approval.decide", "policy.write"
    decision: str  # e.g. "allowed", "denied:policy", "approval_requested", "approved"
    occurred_at: datetime
    agent_id: UUID | None = None
    mission_id: UUID | None = None
    tool: str | None = None
    target: str | None = None
    approval_id: UUID | None = None
    reason: str = ""
    arguments_sha256: str | None = None
    details: Mapping[str, JSONValue] = field(default_factory=dict)
