"""Governance endpoints: approvals, policies, CRITICAL enablement, and the audit log (ADR 0010).

Every write needs an operator token; the operator's name is recorded as the actor.
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, Field

from forge.api.dependencies import clock, operator, uow_factory
from forge.domain.governance import (
    Approval,
    ApprovalStatus,
    AuditRecord,
    Condition,
    Effect,
    Operator,
    Policy,
    Rule,
)
from forge.security import approvals, governance

router = APIRouter(tags=["governance"])


class ApprovalView(BaseModel):
    id: UUID
    mission_id: UUID
    task_id: UUID
    execution_id: UUID
    requesting_agent_id: UUID
    tool: str
    arguments: dict[str, Any]  # the exact action; model-generated, so shown as data
    arguments_sha256: str
    rationale: str
    evidence: list[str]
    affected_resources: list[str]
    risk: str
    status: str
    expires_at: datetime
    requested_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_reason: str

    @classmethod
    def of(cls, a: Approval) -> "ApprovalView":
        return cls(
            id=a.id,
            mission_id=a.mission_id,
            task_id=a.task_id,
            execution_id=a.execution_id,
            requesting_agent_id=a.agent_id,
            tool=a.tool,
            arguments=dict(a.arguments),
            arguments_sha256=a.arguments_sha256,
            rationale=a.rationale,
            evidence=list(a.evidence),
            affected_resources=list(a.affected_resources),
            risk=a.risk,
            status=a.status.value,
            expires_at=a.expires_at,
            requested_at=a.requested_at,
            decided_by=a.decided_by,
            decided_at=a.decided_at,
            decision_reason=a.decision_reason,
        )


class DecisionRequest(BaseModel):
    decision: Literal["approve", "deny"]
    reason: str = Field(min_length=1, max_length=1_000)


class ConditionBody(BaseModel):
    argument: str = Field(min_length=1, max_length=100)
    operator: Operator
    value: Any


class RuleBody(BaseModel):
    effect: Effect
    tools: list[str] = Field(min_length=1, max_length=50)
    agents: list[str] = Field(default_factory=list, max_length=50)
    conditions: list[ConditionBody] = Field(default_factory=list, max_length=20)
    reason: str = Field(default="", max_length=500)

    def rule(self) -> Rule:
        return Rule(
            effect=self.effect,
            tools=tuple(self.tools),
            agents=tuple(self.agents),
            conditions=tuple(Condition(c.argument, c.operator, c.value) for c in self.conditions),
            reason=self.reason,
        )


class PolicyBody(BaseModel):
    name: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    rules: list[RuleBody] = Field(max_length=100)


class PolicyView(BaseModel):
    name: str
    version: int
    rules: list[RuleBody]
    created_by: str
    created_at: datetime

    @classmethod
    def of(cls, p: Policy) -> "PolicyView":
        return cls(
            name=p.name,
            version=p.version,
            rules=[
                RuleBody(
                    effect=r.effect,
                    tools=list(r.tools),
                    agents=list(r.agents),
                    conditions=[
                        ConditionBody(argument=c.argument, operator=c.operator, value=c.value)
                        for c in r.conditions
                    ],
                    reason=r.reason,
                )
                for r in p.rules
            ],
            created_by=p.created_by,
            created_at=p.created_at,
        )


class CriticalRequest(BaseModel):
    enabled: bool
    reason: str = Field(min_length=1, max_length=500)


class AuditView(BaseModel):
    actor: str
    actor_type: str
    action: str
    decision: str
    occurred_at: datetime
    agent_id: UUID | None
    mission_id: UUID | None
    tool: str | None
    target: str | None
    approval_id: UUID | None
    reason: str
    arguments_sha256: str | None
    details: dict[str, Any]

    @classmethod
    def of(cls, r: AuditRecord) -> "AuditView":
        return cls(
            actor=r.actor,
            actor_type=r.actor_type.value,
            action=r.action,
            decision=r.decision,
            occurred_at=r.occurred_at,
            agent_id=r.agent_id,
            mission_id=r.mission_id,
            tool=r.tool,
            target=r.target,
            approval_id=r.approval_id,
            reason=r.reason,
            arguments_sha256=r.arguments_sha256,
            details=dict(r.details),
        )


@router.get("/approvals")
async def list_approvals(
    request: Request,
    status_filter: Annotated[ApprovalStatus | None, Query(alias="status")] = None,
) -> list[ApprovalView]:
    async with uow_factory(request)() as uow:
        return [ApprovalView.of(a) for a in await uow.approvals.list_approvals(status_filter)]


@router.get("/approvals/{approval_id}")
async def get_approval(approval_id: UUID, request: Request) -> ApprovalView:
    async with uow_factory(request)() as uow:
        return ApprovalView.of(await uow.approvals.get(approval_id))


@router.post("/approvals/{approval_id}/decision")
async def decide(
    approval_id: UUID, body: DecisionRequest, request: Request, by: str = Depends(operator)
) -> ApprovalView:
    decided = await approvals.decide(
        uow_factory(request),
        clock(request),
        approval_id,
        approve=body.decision == "approve",
        operator=by,
        reason=body.reason,
    )
    return ApprovalView.of(decided)


@router.get("/policies")
async def list_policies(request: Request) -> list[PolicyView]:
    async with uow_factory(request)() as uow:
        return [PolicyView.of(p) for p in await uow.policies.active()]


@router.post("/policies", status_code=status.HTTP_201_CREATED)
async def write_policy(
    body: PolicyBody, request: Request, by: str = Depends(operator)
) -> PolicyView:
    """A new version of the named policy; earlier versions stay in history."""
    policy = await governance.write_policy(
        uow_factory(request),
        clock(request),
        name=body.name,
        rules=tuple(rule.rule() for rule in body.rules),
        operator=by,
    )
    return PolicyView.of(policy)


@router.get("/critical-tools")
async def critical_tools(request: Request) -> list[str]:
    async with uow_factory(request)() as uow:
        return sorted(await uow.critical.enabled())


@router.post("/tools/{name}/critical", status_code=status.HTTP_204_NO_CONTENT)
async def set_critical(
    name: str, body: CriticalRequest, request: Request, by: str = Depends(operator)
) -> None:
    """Administrator switch for one CRITICAL tool; each call still needs approval."""
    await governance.set_critical(
        uow_factory(request),
        clock(request),
        tool=name,
        enabled=body.enabled,
        operator=by,
        reason=body.reason,
    )


@router.get("/audit")
async def audit_log(
    request: Request,
    mission_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=1_000)] = 200,
) -> list[AuditView]:
    async with uow_factory(request)() as uow:
        records = await uow.audit.list_records(mission_id=mission_id, limit=limit)
    return [AuditView.of(r) for r in records]
