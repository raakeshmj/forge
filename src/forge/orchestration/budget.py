"""Budget Manager: shared mission budgets for concurrent tasks (ADR 0006).

Before each model call the runtime reserves the call's worst-case cost; a step
commit releases it together with recording actual spend, so concurrent tasks
cannot overspend the mission beyond estimation error.
"""

from datetime import datetime
from uuid import UUID

from forge.domain.repositories import UnitOfWork


async def reserve(
    uow: UnitOfWork, mission_id: UUID, execution_id: UUID, amount: int, now: datetime
) -> tuple[bool, int, int]:
    """Reserve `amount` for this execution's next call. Returns (granted, spent, limit)."""
    mission = await uow.missions.get(mission_id, for_update=True)  # serialises reservations
    spent = await uow.executions.mission_spend(mission_id)
    others = await uow.budget.reserved(mission_id, excluding=execution_id)
    limit = mission.budget.limit_micro_usd
    if spent + others + amount > limit:
        return False, spent + others, limit
    await uow.budget.reserve(execution_id, mission_id, amount, now)
    return True, spent + others, limit


async def release(uow: UnitOfWork, execution_id: UUID) -> None:
    await uow.budget.release(execution_id)
