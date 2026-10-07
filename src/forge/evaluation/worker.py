"""Evaluation worker: evaluates each finished mission once (ARCHITECTURE §21, ADR 0007)."""

import asyncio
import contextlib
import logging

from forge.domain.repositories import Clock, UnitOfWorkFactory
from forge.evaluation.judge import JudgeEvaluator
from forge.evaluation.service import evaluate_mission

_log = logging.getLogger(__name__)
BATCH = 10


async def run_until(
    uow_factory: UnitOfWorkFactory,
    clock: Clock,
    stop: asyncio.Event,
    *,
    poll_interval_seconds: float,
    judge: JudgeEvaluator | None = None,
) -> None:
    while not stop.is_set():
        evaluated = await evaluate_pending(uow_factory, clock, judge=judge)
        if not evaluated:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=poll_interval_seconds)


async def evaluate_pending(
    uow_factory: UnitOfWorkFactory, clock: Clock, *, judge: JudgeEvaluator | None = None
) -> int:
    """Evaluate up to BATCH finished missions; concurrent workers never duplicate a record."""
    async with uow_factory() as uow:
        pending = await uow.evaluations.missions_awaiting_evaluation(BATCH)
    done = 0
    for mission_id in pending:
        try:
            if await evaluate_mission(uow_factory, clock, mission_id, trigger="auto", judge=judge):
                done += 1
        except Exception:  # noqa: BLE001 - recovery: log, skip this mission, keep evaluating others
            _log.exception("evaluation failed", extra={"mission_id": str(mission_id)})
    return done
