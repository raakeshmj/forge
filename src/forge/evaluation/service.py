"""Evaluation Service: the single writer of evaluations (ADR 0007)."""

import logging
from uuid import UUID, uuid4

from forge.domain.common import DomainError
from forge.domain.evaluation import (
    Dimension,
    DimensionResult,
    Evaluation,
    Expectations,
    Scope,
    ScopedResult,
)
from forge.domain.events import EvaluationCompleted, EvaluationStarted
from forge.domain.repositories import Clock, UnitOfWorkFactory
from forge.evaluation import benchmarks, evaluators, trajectory
from forge.evaluation.judge import JudgeEvaluator
from forge.observability import tracing

_log = logging.getLogger(__name__)


class MissionNotFinishedError(DomainError):
    def __init__(self, mission_id: UUID) -> None:
        super().__init__(f"mission {mission_id} has not finished; evaluate it once it has")


async def evaluate_mission(
    uow_factory: UnitOfWorkFactory,
    clock: Clock,
    mission_id: UUID,
    *,
    trigger: str,
    judge: JudgeEvaluator | None = None,
) -> Evaluation | None:
    """Evaluate a finished mission. Returns None if an automatic evaluation already exists."""
    async with uow_factory() as uow:
        loaded = await trajectory.load(uow, mission_id)
        reference = await benchmarks.expectations_for(uow, mission_id)
    if not loaded.mission.status.is_terminal:
        raise MissionNotFinishedError(mission_id)
    case, expected = reference if reference else (None, None)

    with tracing.tracer().start_as_current_span(
        "forge.evaluation",
        attributes={
            "forge.mission_id": str(mission_id),
            "forge.evaluator_set": evaluators.EVALUATOR_SET,
            "forge.trigger": trigger,
        },
    ) as span:
        results: list[ScopedResult] = []
        for task in loaded.tasks:
            for result in evaluators.evaluate(task.subject, expected):
                results.append(
                    ScopedResult(
                        Scope.TASK,
                        result,
                        task_id=task.task.id,
                        agent_id=task.task.agent_id,
                        execution_id=task.execution.id if task.execution else None,
                    )
                )
        mission_results, judge_cost = await _mission_results(loaded, expected, judge)
        results.extend(ScopedResult(Scope.MISSION, r) for r in mission_results)
        evaluation = Evaluation(
            id=uuid4(),
            mission_id=mission_id,
            evaluator_set=evaluators.EVALUATOR_SET,
            benchmark_case=case,
            results=tuple(results),
            cost_micro_usd=judge_cost,
            created_at=clock.now(),
        )
        span.set_attribute("forge.evaluation_id", str(evaluation.id))
        for result in mission_results:
            if result.score is not None:
                span.set_attribute(f"forge.score.{result.dimension.value}", result.score)

    async with uow_factory() as uow:
        if not await uow.evaluations.add(evaluation, trigger=trigger):
            return None
        await uow.events.append(
            EvaluationStarted(
                aggregate_id=evaluation.id,
                mission_id=mission_id,
                evaluator_set=evaluation.evaluator_set,
                trigger=trigger,
                benchmark_case=case,
            ),
            correlation_id=loaded.mission.correlation_id,
        )
        await uow.events.append(
            EvaluationCompleted(
                aggregate_id=evaluation.id,
                mission_id=mission_id,
                scores=tuple(
                    f"{r.dimension.value}={'none' if r.score is None else r.score}"
                    for r in mission_results
                ),
                cost_micro_usd=judge_cost,
            ),
            correlation_id=loaded.mission.correlation_id,
        )
        await benchmarks.settle_run_if_done(uow, mission_id, clock.now())
    _log.info("mission evaluated", extra={"mission_id": str(mission_id), "trigger": trigger})
    return evaluation


async def _mission_results(
    loaded: trajectory.MissionTrajectory,
    expected: Expectations | None,
    judge: JudgeEvaluator | None,
) -> tuple[list[DimensionResult], int]:
    results = evaluators.evaluate(loaded.subject, expected)
    if expected is not None or judge is None:
        return results, 0
    verdict = await judge.correctness(loaded.mission, loaded.subject)
    by_dimension = {r.dimension: r for r in results}
    by_dimension[Dimension.CORRECTNESS] = verdict.result
    by_dimension[Dimension.CALIBRATION] = evaluators.calibration(loaded.subject, verdict.result)
    return [by_dimension[d] for d in Dimension], verdict.cost_micro_usd
