"""Approval Service: the single writer of approvals (CLAUDE.md §21, ADR 0010).

An approval binds one exact action (tool, idempotency key, argument hash) and
expires. Only operators decide; an agent has no operator identity, so it can
never approve its own request.
"""

from datetime import datetime
from uuid import UUID

from forge.domain.common import DomainError
from forge.domain.events import ApprovalCompleted, ApprovalRequested
from forge.domain.governance import ActorType, Approval, ApprovalStatus, AuditRecord
from forge.domain.repositories import Clock, UnitOfWork, UnitOfWorkFactory

SYSTEM = "system"


class ApprovalError(DomainError):
    """The decision was refused (already decided, expired, unknown operator)."""


async def request(uow: UnitOfWork, approval: Approval, correlation_id: str | None) -> Approval:
    await uow.approvals.add(approval)
    await uow.events.append(
        ApprovalRequested(
            aggregate_id=approval.id,
            mission_id=approval.mission_id,
            execution_id=approval.execution_id,
            tool=approval.tool,
            risk=approval.risk,
            arguments_sha256=approval.arguments_sha256,
            expires_at=approval.expires_at,
        ),
        correlation_id=correlation_id,
    )
    await uow.audit.add(
        AuditRecord(
            actor=str(approval.agent_id),
            actor_type=ActorType.AGENT,
            action="tool.execute",
            decision="approval_requested",
            occurred_at=approval.requested_at,
            agent_id=approval.agent_id,
            mission_id=approval.mission_id,
            tool=approval.tool,
            target=", ".join(approval.affected_resources) or None,
            approval_id=approval.id,
            reason=approval.rationale[:500],
            arguments_sha256=approval.arguments_sha256,
        )
    )
    return approval


async def decide(
    uow_factory: UnitOfWorkFactory,
    clock: Clock,
    approval_id: UUID,
    *,
    approve: bool,
    operator: str,
    reason: str,
) -> Approval:
    now = clock.now()
    async with uow_factory() as uow:
        approval = await uow.approvals.get(approval_id, for_update=True)
        if approval.status is not ApprovalStatus.PENDING:
            raise ApprovalError(f"approval {approval_id} is already {approval.status}")
        if now < approval.expires_at:
            target = ApprovalStatus.APPROVED if approve else ApprovalStatus.DENIED
            return await _close(
                uow, approval, target, operator, reason, now, actor_type=ActorType.OPERATOR
            )
        # Too late: record the expiry (committed when this block exits), then refuse.
        late = "expired before a decision"
        await _close(uow, approval, ApprovalStatus.EXPIRED, SYSTEM, late, now)
    raise ApprovalError(f"approval {approval_id} expired before it was decided")


async def expire_due(uow: UnitOfWork, now: datetime) -> int:
    """Durable timer: close every pending approval past its expiry (database time order)."""
    due = await uow.approvals.due_for_expiry(now)
    for approval in due:
        await _close(
            uow, approval, ApprovalStatus.EXPIRED, SYSTEM, "no decision before expiry", now
        )
    return len(due)


async def withdraw(uow: UnitOfWork, execution_id: UUID, reason: str, now: datetime) -> None:
    """Close pending approvals of an execution that ended, so no one decides a dead request."""
    for approval in await uow.approvals.pending_for_execution(execution_id):
        await _close(uow, approval, ApprovalStatus.EXPIRED, SYSTEM, reason, now)


async def _close(
    uow: UnitOfWork,
    approval: Approval,
    target: ApprovalStatus,
    by: str,
    reason: str,
    now: datetime,
    *,
    actor_type: ActorType = ActorType.SYSTEM,
) -> Approval:
    decided = await uow.approvals.update(approval.decide(target, by, reason, now))
    await uow.events.append(
        ApprovalCompleted(
            aggregate_id=approval.id,
            mission_id=approval.mission_id,
            decision=target.value,
            decided_by=by,
            reason=reason,
        ),
        correlation_id=None,
    )
    await uow.audit.add(
        AuditRecord(
            actor=by,
            actor_type=actor_type,
            action="approval.decide",
            decision=target.value.lower(),
            occurred_at=now,
            agent_id=approval.agent_id,
            mission_id=approval.mission_id,
            tool=approval.tool,
            approval_id=approval.id,
            reason=reason,
            arguments_sha256=approval.arguments_sha256,
        )
    )
    return decided
