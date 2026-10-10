"""Evaluation worker: evaluates each finished mission once (ARCHITECTURE §21, ADR 0007)."""

import asyncio
import contextlib
import logging

from forge.domain.repositories import (
    Clock,
    StoreUnavailableError,
    UnitOfWorkFactory,
    outage_delay,
)
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
    outages = 0
    while not stop.is_set():
        try:
            evaluated = await evaluate_pending(uow_factory, clock, judge=judge)
            outages, delay = 0, poll_interval_seconds
        except StoreUnavailableError as exc:  # recovery: wait out the outage (ADR 0011)
            outages += 1
            evaluated, delay = 0, outage_delay(outages)
            _log.warning(
                "database unavailable; evaluation backs off",
                extra={"operation": exc.operation, "attempt": outages, "retryable": True},
            )
        if not evaluated:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=delay)


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
