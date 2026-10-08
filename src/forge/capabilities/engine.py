"""Recompute competencies from evaluation evidence (ADR 0008).

Competency is recomputed from all of an agent's evidence whenever a new evaluation
lands, so it is deterministic and corrects itself if evidence is re-evaluated.
"""

from collections import defaultdict
from datetime import datetime
from uuid import UUID, uuid5

from forge.domain.capability import (
    GENERAL,
    Capability,
    Competency,
    Observation,
    estimate,
)
from forge.domain.evaluation import Dimension, DimensionResult, Evaluation
from forge.domain.events import CompetencyUpdated
from forge.domain.repositories import Clock, NotFoundError, UnitOfWork, UnitOfWorkFactory

_NAMESPACE = UUID("5b3a1d7e-21c4-4f0a-8a8e-0f6c1f2d9b44")
_FALLBACK_DIMENSIONS = (
    Dimension.EVIDENCE_QUALITY,
    Dimension.TRAJECTORY_QUALITY,
    Dimension.TOOL_SELECTION,
)

BUILTIN_GRAPH = (
    Capability("incident-response", "Investigate and explain production incidents"),
    Capability(
        "observability", "Read and interpret metrics, logs, traces", parent="incident-response"
    ),
    Capability(
        "change-management", "Understand deployments and changes", parent="incident-response"
    ),
    Capability("planning", "Decompose objectives into task graphs"),
)


async def register_builtin(uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
    async with uow_factory() as uow:
        existing = (await uow.capabilities.graph()).capabilities
        for capability in BUILTIN_GRAPH:
            if capability.name not in existing:
                await uow.capabilities.save(capability, clock.now())


def outcome(results: dict[Dimension, DimensionResult]) -> tuple[float, float] | None:
    """(score, confidence) for one evaluated task: correctness if known, else process quality."""
    correctness = results.get(Dimension.CORRECTNESS)
    if correctness is not None and correctness.score is not None:
        return correctness.score, correctness.confidence
    scored = [
        results[d] for d in _FALLBACK_DIMENSIONS if d in results and results[d].score is not None
    ]
    if not scored:
        return None
    return (
        sum(r.score for r in scored if r.score is not None) / len(scored),
        sum(r.confidence for r in scored) / len(scored),
    )


async def recompute_agent(
    uow: UnitOfWork, agent_id: UUID, now: datetime, *, trigger: UUID
) -> list[Competency]:
    graph = await uow.capabilities.graph()
    grouped: dict[tuple[UUID, UUID], tuple[Evaluation, dict[Dimension, DimensionResult]]] = {}
    for evaluation, scoped in await uow.evaluations.results_for_agent(agent_id):
        if scoped.task_id is None:
            continue
        key = (evaluation.id, scoped.task_id)
        grouped.setdefault(key, (evaluation, {}))[1][scoped.result.dimension] = scoped.result

    observations: dict[str, list[Observation]] = defaultdict(list)
    for (evaluation_id, task_id), (evaluation, results) in grouped.items():
        measured = outcome(results)
        if measured is None:
            continue
        task = await uow.tasks.get(task_id)
        capabilities = set(task.required_capabilities)
        difficulty = 1
        if evaluation.benchmark_case is not None:
            capability, difficulty = await _benchmark_context(uow, evaluation.benchmark_case)
            capabilities.add(capability)
        calibration = results.get(Dimension.CALIBRATION)
        for name, share in graph.attribution(capabilities or {GENERAL}).items():
            observations[name].append(
                Observation(
                    evaluation_id=evaluation_id,
                    score=measured[0],
                    confidence=measured[1],
                    observed_at=evaluation.created_at,
                    difficulty=difficulty,
                    calibration=None if calibration is None else calibration.score,
                    attribution=share,
                )
            )

    updated = []
    for capability, evidence in sorted(observations.items()):
        competency = estimate(agent_id, capability, evidence, now)
        await uow.competencies.save(competency, now)
        await uow.events.append(
            CompetencyUpdated(
                aggregate_id=uuid5(_NAMESPACE, f"{agent_id}:{capability}"),
                mission_id=None,
                agent_id=agent_id,
                capability=capability,
                estimated_score=round(competency.estimated_score, 4),
                uncertainty=round(competency.uncertainty, 4),
                sample_count=competency.sample_count,
                evaluation_id=trigger,
            ),
            correlation_id=None,
        )
        updated.append(competency)
    return updated


async def _benchmark_context(uow: UnitOfWork, reference: str) -> tuple[str, int]:
    """("capability", difficulty) for "name@version/case"."""
    ref, _, case_key = reference.partition("/")
    name, _, version = ref.partition("@")
    try:
        benchmark = await uow.benchmarks.get(name, int(version))
        return benchmark.capability, benchmark.case(case_key).difficulty
    except (NotFoundError, ValueError):
        return GENERAL, 1
