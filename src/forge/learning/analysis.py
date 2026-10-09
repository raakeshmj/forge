"""Failure Analyzer and Capability Gap Detector (ADR 0009).

A gap needs repeated, patterned failure on a capability and a competency whose
optimistic bound is still low. A single failure never creates one.
"""

from collections import Counter
from dataclasses import dataclass
from uuid import UUID

from forge.capabilities.engine import outcome
from forge.domain.capability import GENERAL, Competency
from forge.domain.common import JSONValue
from forge.domain.evaluation import Dimension, DimensionResult, Evaluation
from forge.domain.repositories import UnitOfWork

MIN_FAILURES = 2
MIN_SAMPLES = 3
FAILURE_SCORE = 0.5
GAP_UPPER_BOUND = 0.8


@dataclass(frozen=True, slots=True)
class Example:
    evaluation_id: UUID
    benchmark_case: str | None
    score: float
    codes: tuple[str, ...]
    failed: bool


@dataclass(frozen=True, slots=True)
class FailureAnalysis:
    agent_id: UUID
    capability: str
    examples: tuple[Example, ...]

    @property
    def failures(self) -> tuple[Example, ...]:
        return tuple(e for e in self.examples if e.failed)

    @property
    def pattern(self) -> tuple[str, ...]:
        """Finding codes shared by at least two failures, most common first."""
        counts = Counter(code for e in self.failures for code in set(e.codes))
        return tuple(code for code, n in counts.most_common() if n >= MIN_FAILURES)

    def as_json(self) -> dict[str, JSONValue]:
        return {
            "samples": len(self.examples),
            "failures": len(self.failures),
            "pattern": list(self.pattern),
            "examples": [
                {
                    "evaluation_id": str(e.evaluation_id),
                    "benchmark_case": e.benchmark_case,
                    "score": e.score,
                    "codes": list(e.codes),
                    "failed": e.failed,
                }
                for e in self.examples
            ],
        }


async def analyze(uow: UnitOfWork, agent_id: UUID, capability: str) -> FailureAnalysis:
    graph = await uow.capabilities.graph()
    grouped: dict[tuple[UUID, UUID], tuple[Evaluation, dict[Dimension, DimensionResult]]] = {}
    for evaluation, scoped in await uow.evaluations.results_for_agent(agent_id):
        if scoped.task_id is not None:
            grouped.setdefault((evaluation.id, scoped.task_id), (evaluation, {}))[1][
                scoped.result.dimension
            ] = scoped.result
    examples = []
    for (_, task_id), (evaluation, results) in grouped.items():
        task = await uow.tasks.get(task_id)
        capabilities = set(task.required_capabilities)
        if evaluation.benchmark_case is not None:
            name = evaluation.benchmark_case.split("@")[0]
            for benchmark in await uow.benchmarks.list_all():
                if benchmark.name == name:
                    capabilities.add(benchmark.capability)
        if capability not in graph.attribution(capabilities or {GENERAL}):
            continue
        measured = outcome(results)
        if measured is None:
            continue
        codes = tuple(
            sorted({f.code for r in results.values() if r.score is not None for f in r.findings})
        )
        examples.append(
            Example(
                evaluation.id,
                evaluation.benchmark_case,
                measured[0],
                codes,
                measured[0] <= FAILURE_SCORE,
            )
        )
    return FailureAnalysis(agent_id, capability, tuple(examples))


def gap(analysis: FailureAnalysis, competency: Competency | None) -> tuple[bool, list[str]]:
    """Whether the evidence establishes a capability gap, with the reasons either way."""
    reasons = []
    if len(analysis.examples) < MIN_SAMPLES:
        reasons.append(f"only {len(analysis.examples)} samples; need {MIN_SAMPLES}")
    if len(analysis.failures) < MIN_FAILURES:
        reasons.append(f"only {len(analysis.failures)} failures; need {MIN_FAILURES}")
    if not analysis.pattern:
        reasons.append("failures share no finding pattern")
    if competency is not None:
        upper = competency.estimated_score + competency.uncertainty
        if upper >= GAP_UPPER_BOUND:
            reasons.append(f"competency upper bound {upper:.2f} is not below {GAP_UPPER_BOUND}")
    return (not reasons), reasons
