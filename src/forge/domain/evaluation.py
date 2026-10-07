"""Evaluations and benchmarks (docs/EVALUATION.md, docs/DOMAIN_MODEL.md §17-18, ADR 0007).

An evaluation never collapses quality into one number: each dimension has its
own score (or None when it cannot be scored honestly), the evaluator's
confidence, structured findings that point into the trajectory, and the raw
metrics behind the score.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from forge.domain.common import DomainError, JSONValue


class Dimension(StrEnum):
    CORRECTNESS = "correctness"
    EVIDENCE_QUALITY = "evidence_quality"
    TRAJECTORY_QUALITY = "trajectory_quality"
    TOOL_SELECTION = "tool_selection"
    EFFICIENCY = "efficiency"
    CALIBRATION = "calibration"
    SAFETY = "safety"
    COST_EFFICIENCY = "cost_efficiency"


class Scope(StrEnum):
    TASK = "task"
    MISSION = "mission"


@dataclass(frozen=True, slots=True)
class Finding:
    code: str  # stable machine-readable reason, e.g. "duplicate_tool_call"
    message: str
    refs: tuple[str, ...] = ()  # e.g. "execution:<id>", "turn:2", "tool_use:<id>"


@dataclass(frozen=True, slots=True)
class DimensionResult:
    dimension: Dimension
    score: float | None  # None: not enough information to score honestly
    confidence: float
    evaluator: str  # name@version
    findings: tuple[Finding, ...] = ()
    metrics: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.score is not None and not 0.0 <= self.score <= 1.0:
            raise DomainError(f"{self.dimension} score must be within [0, 1]")
        if not 0.0 <= self.confidence <= 1.0:
            raise DomainError("confidence must be within [0, 1]")


@dataclass(frozen=True, slots=True)
class ScopedResult:
    scope: Scope
    result: DimensionResult
    task_id: UUID | None = None
    agent_id: UUID | None = None
    execution_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class Evaluation:
    id: UUID
    mission_id: UUID
    evaluator_set: str
    benchmark_case: str | None  # "benchmark@version/case" when ground truth was used
    results: tuple[ScopedResult, ...]
    cost_micro_usd: int  # what evaluating cost (an LLM judge), not the mission
    created_at: datetime

    def mission_result(self, dimension: Dimension) -> DimensionResult | None:
        for scoped in self.results:
            if scoped.scope is Scope.MISSION and scoped.result.dimension is dimension:
                return scoped.result
        return None


# --- Benchmarks --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Expectations:
    """Ground truth for one case (EVALUATION §14)."""

    must_include: tuple[str, ...] = ()  # phrases a correct answer contains (case-insensitive)
    must_not_include: tuple[str, ...] = ()
    expected_tools: tuple[str, ...] = ()
    reference_evidence: tuple[str, ...] = ()  # values the evidence should surface
    ideal_turns: int = 2
    reference_cost_micro_usd: int | None = None


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    key: str
    objective: str
    expectations: Expectations
    required_capabilities: tuple[str, ...] = ()
    difficulty: int = 1  # 1 (easy) .. 5 (hard)
    risk: str = "LOW"


@dataclass(frozen=True, slots=True)
class Benchmark:
    name: str
    version: int
    capability: str
    description: str
    cases: tuple[BenchmarkCase, ...]

    def __post_init__(self) -> None:
        keys = [case.key for case in self.cases]
        if not keys or len(set(keys)) != len(keys):
            raise DomainError("a benchmark needs cases with unique keys")

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"

    def case(self, key: str) -> BenchmarkCase:
        for case in self.cases:
            if case.key == key:
                return case
        raise DomainError(f"{self.ref} has no case {key!r}")


class BenchmarkRunStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"


@dataclass(frozen=True, slots=True)
class BenchmarkRun:
    id: UUID
    benchmark: str  # name
    version: int
    agent_id: UUID
    status: BenchmarkRunStatus
    summary: Mapping[str, JSONValue]
    created_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class BenchmarkRunItem:
    run_id: UUID
    case_key: str
    mission_id: UUID
