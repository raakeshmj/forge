"""Operator-side governance writes: policies and CRITICAL enablement, always audited (ADR 0010)."""

from forge.domain.governance import ActorType, AuditRecord, Policy, Rule
from forge.domain.repositories import Clock, UnitOfWorkFactory


async def write_policy(
    uow_factory: UnitOfWorkFactory,
    clock: Clock,
    *,
    name: str,
    rules: tuple[Rule, ...],
    operator: str,
) -> Policy:
    now = clock.now()
    async with uow_factory() as uow:
        policy = Policy(name, await uow.policies.next_version(name), rules, operator, now)
        await uow.policies.add(policy)
        await uow.audit.add(
            AuditRecord(
                actor=operator,
                actor_type=ActorType.OPERATOR,
                action="policy.write",
                decision="written",
                occurred_at=now,
                target=f"{policy.name}@{policy.version}",
                details={"rules": len(rules)},
            )
        )
    return policy


async def set_critical(
    uow_factory: UnitOfWorkFactory,
    clock: Clock,
    *,
    tool: str,
    enabled: bool,
    operator: str,
    reason: str,
) -> None:
    now = clock.now()
    async with uow_factory() as uow:
        await uow.critical.set(tool, enabled, operator, reason, now)
        await uow.audit.add(
            AuditRecord(
                actor=operator,
                actor_type=ActorType.OPERATOR,
                action="critical.enable" if enabled else "critical.disable",
                decision="enabled" if enabled else "disabled",
                occurred_at=now,
                tool=tool,
                reason=reason,
            )
        )
