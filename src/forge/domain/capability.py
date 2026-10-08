"""Capabilities, competency, reliability, and task fitness (ADR 0008).

Pure functions over evidence: no I/O, deterministic, and every estimate keeps
its uncertainty, sample count, trend, and supporting evidence alongside it.
"""

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from forge.domain.common import DomainError

GENERAL = "general"
HALF_LIFE_DAYS = 14.0
TREND_WINDOW = 5
ANCESTOR_WEIGHT = 0.5
CALIBRATION_TOLERANCE = 0.8


# --- Graph -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Capability:
    name: str
    description: str = ""
    parent: str | None = None
    prerequisites: tuple[str, ...] = ()
    related: tuple[str, ...] = ()
    required_tools: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name or self.parent == self.name:
            raise DomainError(f"invalid capability {self.name!r}")


@dataclass(frozen=True, slots=True)
class CapabilityGraph:
    capabilities: Mapping[str, Capability] = field(default_factory=dict)

    def ancestors(self, name: str) -> tuple[str, ...]:
        chain: list[str] = []
        current = self.capabilities.get(name)
        while current is not None and current.parent is not None:
            if current.parent in chain or current.parent == name:
                raise DomainError(f"capability graph has a parent cycle at {name!r}")
            chain.append(current.parent)
            current = self.capabilities.get(current.parent)
        return tuple(chain)

    def serves(self, declared: Iterable[str], required: str) -> bool:
        """An agent serves `required` if it declares it or a descendant, plus prerequisites."""
        declared = set(declared)
        covering = required in declared or any(required in self.ancestors(d) for d in declared)
        if not covering:
            return False
        prerequisites = self.capabilities.get(required, Capability(required)).prerequisites
        return all(self.serves(declared, p) for p in prerequisites)

    def attribution(self, capabilities: Iterable[str]) -> dict[str, float]:
        """Weight each capability's share of one piece of evidence (ancestors at half)."""
        weights: dict[str, float] = {}
        for name in capabilities:
            weights[name] = max(weights.get(name, 0.0), 1.0)
            for ancestor in self.ancestors(name):
                weights[ancestor] = max(weights.get(ancestor, 0.0), ANCESTOR_WEIGHT)
        return weights


# --- Competency --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Observation:
    """One evaluated task outcome counting as evidence for a capability."""

    evaluation_id: UUID
    score: float  # 0..1 outcome
    confidence: float  # evaluator confidence
    observed_at: datetime
    difficulty: int = 1
    calibration: float | None = None
    attribution: float = 1.0  # 1 for the capability itself, 0.5 for an ancestor


@dataclass(frozen=True, slots=True)
class Competency:
    agent_id: UUID
    capability: str
    alpha: float
    beta: float
    sample_count: int
    effective_samples: float
    recent_trend: float | None
    calibration: float | None
    last_evaluated: datetime | None
    supporting_evaluations: tuple[UUID, ...]

    @property
    def estimated_score(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    @property
    def uncertainty(self) -> float:
        total = self.alpha + self.beta
        return math.sqrt(self.alpha * self.beta / (total * total * (total + 1)))


def prior(agent_id: UUID, capability: str) -> Competency:
    """No evidence: uniform Beta(1, 1), estimate 0.5, maximal uncertainty."""
    return Competency(agent_id, capability, 1.0, 1.0, 0, 0.0, None, None, None, ())


def estimate(
    agent_id: UUID, capability: str, observations: Sequence[Observation], now: datetime
) -> Competency:
    alpha, beta, effective = 1.0, 1.0, 0.0
    ordered = sorted(observations, key=lambda o: o.observed_at)
    for observation in ordered:
        age_days = max(0.0, (now - observation.observed_at).total_seconds() / 86_400)
        recency = 0.5 ** (age_days / HALF_LIFE_DAYS)
        difficulty = 1 + 0.25 * (max(1, observation.difficulty) - 1)
        weight = observation.confidence * recency * difficulty * observation.attribution
        alpha += weight * observation.score
        beta += weight * (1 - observation.score)
        effective += weight
    scores = [o.score for o in ordered]
    trend = None
    if len(scores) >= 2 * TREND_WINDOW:
        recent, earlier = scores[-TREND_WINDOW:], scores[-2 * TREND_WINDOW : -TREND_WINDOW]
        trend = round(sum(recent) / TREND_WINDOW - sum(earlier) / TREND_WINDOW, 4)
    elif len(scores) >= 2:
        half = len(scores) // 2
        trend = round(sum(scores[half:]) / (len(scores) - half) - sum(scores[:half]) / half, 4)
    calibrations = [o.calibration for o in ordered if o.calibration is not None]
    return Competency(
        agent_id=agent_id,
        capability=capability,
        alpha=round(alpha, 6),
        beta=round(beta, 6),
        sample_count=len(ordered),
        effective_samples=round(effective, 4),
        recent_trend=trend,
        calibration=round(sum(calibrations) / len(calibrations), 4) if calibrations else None,
        last_evaluated=ordered[-1].observed_at if ordered else None,
        supporting_evaluations=tuple(dict.fromkeys(o.evaluation_id for o in ordered)),
    )


# --- Reliability -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Reliability:
    """Execution track record attributable to the agent (ADR 0008)."""

    successes: int = 0
    failures: int = 0
    mean_cost_micro_usd: float | None = None
    mean_latency_ms: float | None = None

    @property
    def estimate(self) -> float:
        return (1 + self.successes) / (2 + self.successes + self.failures)

    @property
    def uncertainty(self) -> float:
        a, b = 1 + self.successes, 1 + self.failures
        return math.sqrt(a * b / ((a + b) ** 2 * (a + b + 1)))


# --- Fitness -----------------------------------------------------------------------------

WEIGHTS = {"ability": 0.45, "reliability": 0.20, "cost": 0.15, "latency": 0.10, "workload": 0.10}


@dataclass(frozen=True, slots=True)
class Fitness:
    agent_id: UUID
    name: str
    score: float
    components: Mapping[str, float]

    def describe(self) -> str:
        parts = ", ".join(f"{k} {v:.2f}" for k, v in self.components.items())
        return f"{self.name}: fitness {self.score:.3f} ({parts})"


@dataclass(frozen=True, slots=True)
class Candidate:
    agent_id: UUID
    name: str
    competencies: Mapping[str, Competency]  # by required capability
    reliability: Reliability
    workload: int


def fitness(candidates: Sequence[Candidate], *, optimistic: bool) -> list[Fitness]:
    """Score every candidate; best first. Optimistic (UCB) explores; pessimistic (LCB) exploits."""
    sign = 1.0 if optimistic else -1.0
    costs = [
        c.reliability.mean_cost_micro_usd for c in candidates if c.reliability.mean_cost_micro_usd
    ]
    latencies = [c.reliability.mean_latency_ms for c in candidates if c.reliability.mean_latency_ms]
    scored = []
    for candidate in candidates:
        bounds = [
            min(1.0, max(0.0, comp.estimated_score + sign * comp.uncertainty))
            for comp in candidate.competencies.values()
        ] or [0.5]
        ability = min(bounds)
        calibrations = [
            c.calibration for c in candidate.competencies.values() if c.calibration is not None
        ]
        calibration = min(calibrations) if calibrations else 1.0
        calibration_factor = (
            1.0 if calibration >= CALIBRATION_TOLERANCE else calibration / CALIBRATION_TOLERANCE
        )
        rel = candidate.reliability
        reliability = min(1.0, max(0.0, rel.estimate + sign * rel.uncertainty))
        cost = _relative(rel.mean_cost_micro_usd, min(costs) if costs else None)
        latency = _relative(rel.mean_latency_ms, min(latencies) if latencies else None)
        load = min(1.0, candidate.workload / 5)
        components = {
            "ability": round(ability, 4),
            "calibration": round(calibration_factor, 4),
            "reliability": round(reliability, 4),
            "cost": round(cost, 4),
            "latency": round(latency, 4),
            "workload": candidate.workload,
        }
        score = (
            WEIGHTS["ability"] * ability * calibration_factor
            + WEIGHTS["reliability"] * reliability
            + WEIGHTS["cost"] * cost
            + WEIGHTS["latency"] * latency
            - WEIGHTS["workload"] * load
        )
        scored.append(Fitness(candidate.agent_id, candidate.name, round(score, 6), components))
    return sorted(scored, key=lambda f: (-f.score, f.components["workload"], f.name))


def _relative(value: float | None, best: float | None) -> float:
    if value is None or best is None or value <= 0:
        return 0.5  # no history: neutral
    return min(1.0, best / value)
