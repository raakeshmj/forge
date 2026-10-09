"""Learning worker: advances validation, monitors promotions, optionally detects gaps (ADR 0009)."""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable
from uuid import UUID

from forge.domain.repositories import Clock, UnitOfWorkFactory
from forge.domain.skill import CycleStatus
from forge.learning import analysis, cycle, generation

_log = logging.getLogger(__name__)
_LIVE = {
    CycleStatus.ANALYZED,
    CycleStatus.GAP_DETECTED,
    CycleStatus.NEEDS_DATASET,
    CycleStatus.CANDIDATE_READY,
    CycleStatus.VALIDATING,
    CycleStatus.VALIDATED,
    CycleStatus.PROMOTED,
}


async def run_until(
    uow_factory: UnitOfWorkFactory,
    clock: Clock,
    stop: asyncio.Event,
    *,
    poll_interval_seconds: float,
    generator: generation.SkillGenerator,
    auto_detect: bool,
) -> None:
    while not stop.is_set():
        await step(uow_factory, clock, generator=generator, auto_detect=auto_detect)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=poll_interval_seconds)


async def step(
    uow_factory: UnitOfWorkFactory,
    clock: Clock,
    *,
    generator: generation.SkillGenerator,
    auto_detect: bool,
) -> None:
    async with uow_factory() as uow:
        validating = await uow.learning.in_status(CycleStatus.VALIDATING)
        promoted = await uow.learning.in_status(CycleStatus.PROMOTED)
    for item in validating:
        await _guarded(cycle.advance(uow_factory, clock, item.id), item.id)
    for item in promoted:
        await _guarded(cycle.monitor(uow_factory, clock, item.id), item.id)
    if auto_detect:
        await detect(uow_factory, clock, generator=generator)


async def detect(
    uow_factory: UnitOfWorkFactory, clock: Clock, *, generator: generation.SkillGenerator
) -> int:
    """Start a cycle for each (agent, capability) whose competency suggests a gap."""
    async with uow_factory() as uow:
        agents = [agent.id for agent, _ in await uow.agents.list_current()]
        competencies = await uow.competencies.for_agents(agents)
        live = {
            (c.agent_id, c.capability)
            for c in await uow.learning.list_cycles()
            if c.status in _LIVE
        }
        known_tools = {tool.name for tool in await uow.tool_registry.list()}
    started = 0
    for competency in competencies:
        upper = competency.estimated_score + competency.uncertainty
        key = (competency.agent_id, competency.capability)
        if (
            competency.sample_count >= analysis.MIN_SAMPLES
            and upper < analysis.GAP_UPPER_BOUND
            and key not in live
        ):
            await cycle.start(
                uow_factory,
                clock,
                competency.agent_id,
                competency.capability,
                generator=generator,
                known_tools=known_tools,
            )
            started += 1
    return started


async def _guarded(operation: Awaitable[object], cycle_id: UUID) -> None:
    try:
        await operation
    except Exception:  # noqa: BLE001 - recovery: log; the cycle is retried on the next pass
        _log.exception("learning step failed", extra={"cycle": str(cycle_id)})
