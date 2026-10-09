"""Learning cycles: the durable state machine driving controlled improvement (ADR 0009).

start -> (gap?) -> candidate skill + held-out dataset -> A/B validation runs ->
validated or rejected -> operator promotion -> monitoring -> rollback if it regresses.
Nothing here changes an agent's behaviour except `promote` and `rollback`, which
only move the agent between immutable versions.
"""

import logging
from dataclasses import replace
from datetime import datetime
from uuid import UUID, uuid4

from forge.domain.agent import AgentVersion
from forge.domain.common import JSONValue
from forge.domain.evaluation import Benchmark, BenchmarkRunStatus, Dimension, Scope
from forge.domain.events import (
    CapabilityGapDetected,
    DomainEvent,
    SkillCandidateCreated,
    SkillPromoted,
    SkillRolledBack,
    SkillValidated,
)
from forge.domain.repositories import Clock, UnitOfWork, UnitOfWorkFactory
from forge.domain.skill import CycleStatus, LearningCycle, LearningError, SkillStatus, SkillVersion
from forge.evaluation import benchmarks
from forge.learning import analysis, generation, validation
from forge.observability import tracing

_log = logging.getLogger(__name__)

MONITOR_MIN_EVALUATIONS = 3
MONITOR_TOLERANCE = 0.2


async def start(
    uow_factory: UnitOfWorkFactory,
    clock: Clock,
    agent_id: UUID,
    capability: str,
    *,
    generator: generation.SkillGenerator,
    known_tools: set[str],
) -> LearningCycle:
    now = clock.now()
    cycle_id = uuid4()
    with tracing.tracer().start_as_current_span(
        "forge.learning",
        attributes={"forge.capability_gap": capability, "forge.agent_id": str(agent_id)},
    ) as span:
        async with uow_factory() as uow:
            found = await analysis.analyze(uow, agent_id, capability)
            competencies = {c.capability: c for c in await uow.competencies.for_agents([agent_id])}
            version = await uow.agents.get_current_version(agent_id)
        has_gap, reasons = analysis.gap(found, competencies.get(capability))
        cycle = LearningCycle(
            id=cycle_id,
            agent_id=agent_id,
            capability=capability,
            status=CycleStatus.ANALYZED,
            analysis=found.as_json(),
            gap={"reasons": list(reasons)},
            skill=None,
            dataset=None,
            baseline_version_id=version.id,
            candidate_version_id=None,
            baseline_run_id=None,
            candidate_run_id=None,
            delta={},
            created_at=now,
            updated_at=now,
        )
        if not has_gap:
            cycle = cycle.transition(CycleStatus.NO_GAP, now)
            span.set_attribute("forge.promotion_decision", "no_gap")
            async with uow_factory() as uow:
                await uow.learning.add(cycle)
            return cycle

        cycle = cycle.transition(
            CycleStatus.GAP_DETECTED, now, gap={"pattern": list(found.pattern)}
        )
        events: list[DomainEvent] = [
            CapabilityGapDetected(
                aggregate_id=cycle_id,
                mission_id=None,
                agent_id=agent_id,
                capability=capability,
                failing_evaluations=len(found.failures),
                pattern=found.pattern,
            )
        ]
        source, failing, passing = await _ground_truth(uow_factory, found)
        if source is None or len(failing) < analysis.MIN_FAILURES:
            cycle = cycle.transition(CycleStatus.NEEDS_DATASET, now)
            async with uow_factory() as uow:
                await uow.learning.add(cycle)
                await _emit(uow, events)
            return cycle

        instructions = await generator.generate(found)
        skill_name = f"{capability}-practice"
        dataset = generation.dataset(source, failing, passing, cycle_id)
        async with uow_factory() as uow:
            await uow.benchmarks.add(dataset, now)
            skill = SkillVersion(
                name=skill_name,
                version=await uow.skills.next_version(skill_name),
                capability=capability,
                instructions=instructions,
                status=SkillStatus.CANDIDATE,
                provenance={
                    "source": "generated",
                    "generator": generator.name,
                    "cycle": str(cycle_id),
                    "pattern": list(found.pattern),
                    "failing_evaluations": [str(e.evaluation_id) for e in found.failures],
                    "baseline_agent_version": str(version.id),
                },
                parent_version=None,
                created_at=now,
            )
            await uow.skills.add(skill)
            candidate = _candidate_version(
                version, skill, len(await uow.agents.list_versions(agent_id)) + 1, now
            )
            await uow.agents.add_version(candidate)
        events.append(
            SkillCandidateCreated(
                aggregate_id=cycle_id,
                mission_id=None,
                skill=skill.name,
                version=skill.version,
                generator=generator.name,
                dataset=dataset.ref,
            )
        )
        cycle = cycle.transition(
            CycleStatus.CANDIDATE_READY,
            now,
            skill=skill.ref,
            dataset=dataset.ref,
            candidate_version_id=candidate.id,
        )
        span.set_attributes(
            {
                "forge.candidate_skill": f"{skill.name}@{skill.version}",
                "forge.benchmark": dataset.ref,
            }
        )

        problems = validation.content_problems(instructions, version, known_tools)
        if problems:
            return await _reject(uow_factory, now, cycle, skill, problems, events)

        baseline_run = await benchmarks.start_run(
            uow_factory,
            clock,
            benchmark=dataset.name,
            version=dataset.version,
            agent_id=agent_id,
            agent_version_id=version.id,
        )
        candidate_run = await benchmarks.start_run(
            uow_factory,
            clock,
            benchmark=dataset.name,
            version=dataset.version,
            agent_id=agent_id,
            agent_version_id=candidate.id,
        )
        cycle = cycle.transition(
            CycleStatus.VALIDATING,
            now,
            baseline_run_id=baseline_run.id,
            candidate_run_id=candidate_run.id,
        )
        async with uow_factory() as uow:
            await uow.learning.add(cycle)
            await _emit(uow, events)
    return cycle


async def advance(uow_factory: UnitOfWorkFactory, clock: Clock, cycle_id: UUID) -> LearningCycle:
    """Decide a VALIDATING cycle once both validation runs are complete."""
    now = clock.now()
    async with uow_factory() as uow:
        cycle = await uow.learning.get(cycle_id, for_update=True)
        if cycle.status is not CycleStatus.VALIDATING:
            return cycle
        if not (cycle.baseline_run_id and cycle.candidate_run_id and cycle.skill):
            raise LearningError(f"cycle {cycle.id} is VALIDATING without its runs")
        baseline = await uow.benchmarks.get_run(cycle.baseline_run_id)
        candidate = await uow.benchmarks.get_run(cycle.candidate_run_id)
        if BenchmarkRunStatus.RUNNING in (baseline.status, candidate.status):
            return cycle
        passed, reasons, delta = validation.compare(baseline.summary, candidate.summary)
        skill = await uow.skills.get(cycle.skill.name, cycle.skill.version, for_update=True)
        target = delta.get("target_correctness")
        with tracing.tracer().start_as_current_span(
            "forge.learning",
            attributes={
                "forge.capability_gap": cycle.capability,
                "forge.candidate_skill": f"{skill.name}@{skill.version}",
                "forge.benchmark": cycle.dataset or "",
                "forge.before_score": _correctness(baseline.summary),
                "forge.after_score": _correctness(candidate.summary),
                "forge.promotion_decision": "validated" if passed else "rejected",
            },
        ):
            record: dict[str, JSONValue] = {
                "passed": passed,
                "reasons": list(reasons),
                "delta": delta,
                "baseline_run": str(baseline.id),
                "candidate_run": str(candidate.id),
            }
            skill = await uow.skills.update(
                replace(skill, validation=record).transition(
                    SkillStatus.VALIDATED if passed else SkillStatus.REJECTED
                )
            )
            delta["candidate_correctness"] = _correctness(candidate.summary)
            delta["baseline_correctness"] = _correctness(baseline.summary)
            cycle = await uow.learning.update(
                cycle.transition(
                    CycleStatus.VALIDATED if passed else CycleStatus.REJECTED, now, delta=delta
                )
            )
            await _emit(
                uow,
                [
                    SkillValidated(
                        aggregate_id=cycle.id,
                        mission_id=None,
                        skill=skill.name,
                        version=skill.version,
                        passed=passed,
                        reasons=tuple(reasons),
                        correctness_delta=target if isinstance(target, float) else None,
                    )
                ],
            )
    return cycle


async def promote(uow_factory: UnitOfWorkFactory, clock: Clock, cycle_id: UUID) -> LearningCycle:
    """Operator action: make the validated candidate the agent's current version."""
    now = clock.now()
    async with uow_factory() as uow:
        cycle = await uow.learning.get(cycle_id, for_update=True)
        if cycle.status is not CycleStatus.VALIDATED:
            raise LearningError(
                f"only a VALIDATED cycle can be promoted; this one is {cycle.status}"
            )
        if not (cycle.skill and cycle.candidate_version_id and cycle.baseline_version_id):
            raise LearningError(f"cycle {cycle.id} is VALIDATED without a candidate")
        candidate = await uow.agents.get_version(cycle.candidate_version_id)
        baseline = await uow.agents.get_version(cycle.baseline_version_id)
        current = await uow.agents.get_current_version(cycle.agent_id)
        if current.id != baseline.id:
            raise LearningError("the agent changed since validation; start a new cycle")
        skill = await uow.skills.get(cycle.skill.name, cycle.skill.version, for_update=True)
        for other in await uow.skills.list_all():
            if other.name == skill.name and other.status is SkillStatus.PROMOTED:
                await uow.skills.update(other.transition(SkillStatus.DEPRECATED))
        await uow.skills.update(skill.transition(SkillStatus.PROMOTED))
        await uow.agents.set_current(cycle.agent_id, candidate.version)
        cycle = await uow.learning.update(cycle.transition(CycleStatus.PROMOTED, now))
        await _emit(
            uow,
            [
                SkillPromoted(
                    aggregate_id=cycle.id,
                    mission_id=None,
                    skill=skill.name,
                    version=skill.version,
                    agent_id=cycle.agent_id,
                    agent_version=candidate.version,
                    previous_agent_version=baseline.version,
                )
            ],
        )
    _log.info("skill promoted", extra={"cycle": str(cycle.id), "skill": skill.name})
    return cycle


async def rollback(
    uow_factory: UnitOfWorkFactory, clock: Clock, cycle_id: UUID, *, reason: str
) -> LearningCycle:
    """Restore the agent version that preceded the promotion."""
    now = clock.now()
    async with uow_factory() as uow:
        cycle = await uow.learning.get(cycle_id, for_update=True)
        if cycle.status is not CycleStatus.PROMOTED:
            raise LearningError(
                f"only a PROMOTED cycle can be rolled back; this one is {cycle.status}"
            )
        if not (cycle.skill and cycle.baseline_version_id):
            raise LearningError(f"cycle {cycle.id} is PROMOTED without a skill")
        baseline = await uow.agents.get_version(cycle.baseline_version_id)
        await uow.agents.set_current(cycle.agent_id, baseline.version)
        skill = await uow.skills.get(cycle.skill.name, cycle.skill.version, for_update=True)
        if skill.status is SkillStatus.PROMOTED:
            await uow.skills.update(skill.transition(SkillStatus.DEPRECATED))
        cycle = await uow.learning.update(
            cycle.transition(
                CycleStatus.ROLLED_BACK, now, delta={**cycle.delta, "rollback_reason": reason}
            )
        )
        await _emit(
            uow,
            [
                SkillRolledBack(
                    aggregate_id=cycle.id,
                    mission_id=None,
                    skill=skill.name,
                    version=skill.version,
                    agent_id=cycle.agent_id,
                    restored_agent_version=baseline.version,
                    reason=reason,
                )
            ],
        )
    _log.warning("skill rolled back", extra={"cycle": str(cycle.id), "reason": reason})
    return cycle


async def monitor(uow_factory: UnitOfWorkFactory, clock: Clock, cycle_id: UUID) -> LearningCycle:
    """Roll back a promoted skill whose live correctness falls well below its validation."""
    async with uow_factory() as uow:
        cycle = await uow.learning.get(cycle_id)
        if cycle.status is not CycleStatus.PROMOTED or cycle.candidate_version_id is None:
            return cycle
        observed = []
        for evaluation, scoped in await uow.evaluations.results_for_agent(cycle.agent_id):
            if (
                scoped.scope is Scope.TASK
                and scoped.result.dimension is Dimension.CORRECTNESS
                and scoped.result.score is not None
                and evaluation.created_at > cycle.updated_at
                and scoped.execution_id is not None
                and (await uow.executions.get(scoped.execution_id)).agent_version_id
                == cycle.candidate_version_id
            ):
                observed.append(scoped.result.score)
    if len(observed) < MONITOR_MIN_EVALUATIONS:
        return cycle
    expected = _validated_correctness(cycle)
    live = sum(observed) / len(observed)
    if expected is not None and live < expected - MONITOR_TOLERANCE:
        return await rollback(
            uow_factory,
            clock,
            cycle_id,
            reason=f"live correctness {live:.2f} fell below validated {expected:.2f}",
        )
    return cycle


def _validated_correctness(cycle: LearningCycle) -> float | None:
    value = cycle.delta.get("candidate_correctness")
    return float(value) if isinstance(value, (int, float)) else None


async def _ground_truth(
    uow_factory: UnitOfWorkFactory, found: analysis.FailureAnalysis
) -> tuple[Benchmark | None, list[str], list[str]]:
    """The benchmark the failures came from, its failing cases, and cases already passed."""
    refs = [e.benchmark_case for e in found.examples if e.benchmark_case]
    if not refs:
        return None, [], []
    source_ref = refs[-1].split("/")[0]
    name, _, version = source_ref.partition("@")
    async with uow_factory() as uow:
        source = await uow.benchmarks.get(name, int(version))
    failing = sorted(
        {
            e.benchmark_case.split("/")[1]
            for e in found.failures
            if e.benchmark_case and e.benchmark_case.startswith(source_ref)
        }
    )
    passing = sorted(
        {
            e.benchmark_case.split("/")[1]
            for e in found.examples
            if not e.failed and e.benchmark_case and e.benchmark_case.startswith(source_ref)
        }
        - set(failing)
    )
    return source, failing, passing


def _candidate_version(
    version: AgentVersion, skill: SkillVersion, number: int, now: datetime
) -> AgentVersion:
    skills = (*(s for s in version.skills if s.name != skill.name), skill.ref)
    return replace(version, id=uuid4(), version=number, skills=skills, created_at=now)


async def _reject(
    uow_factory: UnitOfWorkFactory,
    now: datetime,
    cycle: LearningCycle,
    skill: SkillVersion,
    problems: list[str],
    events: list[DomainEvent],
) -> LearningCycle:
    async with uow_factory() as uow:
        stored = await uow.skills.get(skill.name, skill.version, for_update=True)
        await uow.skills.update(
            replace(
                stored, validation={"passed": False, "reasons": list[JSONValue](problems)}
            ).transition(SkillStatus.REJECTED)
        )
        cycle = cycle.transition(CycleStatus.REJECTED, now, delta={"reasons": list(problems)})
        await uow.learning.add(cycle)
        await _emit(
            uow,
            [
                *events,
                SkillValidated(
                    aggregate_id=cycle.id,
                    mission_id=None,
                    skill=skill.name,
                    version=skill.version,
                    passed=False,
                    reasons=tuple(problems),
                    correctness_delta=None,
                ),
            ],
        )
    return cycle


async def _emit(uow: UnitOfWork, events: list[DomainEvent]) -> None:
    for event in events:
        await uow.events.append(event, correlation_id=None)


def _correctness(summary: object) -> float:
    if isinstance(summary, dict):
        means = summary.get("mean_scores")
        if isinstance(means, dict) and isinstance(means.get("correctness"), (int, float)):
            return float(means["correctness"])
    return -1.0
