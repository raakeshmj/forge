"""Benchmark metrics over per-mission samples (ADR 0013). Pure and deterministic."""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from forge.evaluation.benchmarks import expected_calibration_error


@dataclass(frozen=True, slots=True)
class MissionSample:
    """Everything measured about one benchmark mission, read from the system of record."""

    mission_id: UUID
    case: str
    status: str
    passed: bool  # correctness against ground truth at or above the pass threshold
    correctness: float | None
    evidence_quality: float | None
    stated_confidence: float | None
    tool_calls: int
    latency_ms: float  # end to end: submitted to terminal
    model_latencies_ms: tuple[float, ...]
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_micro_usd: int
    cache_savings_micro_usd: int
    crashed: bool  # some execution needed more than one attempt
    failure_kinds: tuple[str, ...] = ()  # of failed executions, as recorded


@dataclass(frozen=True, slots=True)
class Percentiles:
    p50: float | None
    p95: float | None
    p99: float | None


@dataclass(frozen=True, slots=True)
class ArmResult:
    name: str
    description: str
    missions: int
    passes: int
    success_rate: float
    completion_rate: float
    mean_correctness: float | None
    mean_evidence_quality: float | None
    tool_calls_per_mission: float
    mission_latency_ms: Percentiles
    model_latency_ms: Percentiles
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cache_hit_rate: float
    cache_savings_micro_usd: int
    cost_micro_usd: int
    cost_per_success_micro_usd: int | None
    expected_calibration_error: float | None
    recovery_rate: float | None  # completed ÷ crashed; None when nothing crashed
    duplicate_rate: float | None  # repeated effects ÷ effects; None when not measured
    failures_by_kind: dict[str, int]


def percentile(values: Sequence[float], q: float) -> float | None:
    """Linear interpolation between closest ranks (the common 'type 7' definition)."""
    if not values:
        return None
    if not 0 <= q <= 1:
        raise ValueError("q must be within [0, 1]")
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def percentiles(values: Sequence[float]) -> Percentiles:
    return Percentiles(percentile(values, 0.5), percentile(values, 0.95), percentile(values, 0.99))


def _mean(values: Sequence[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else None


def summarize(
    name: str,
    description: str,
    samples: Sequence[MissionSample],
    *,
    duplicate_rate: float | None = None,
) -> ArmResult:
    passes = sum(s.passed for s in samples)
    cost = sum(s.cost_micro_usd for s in samples)
    read = sum(s.cache_read_tokens for s in samples)
    prompt = sum(s.input_tokens + s.cache_read_tokens + s.cache_write_tokens for s in samples)
    crashed = [s for s in samples if s.crashed]
    calibration = [
        (s.stated_confidence, 1.0 if s.passed else 0.0)
        for s in samples
        if s.stated_confidence is not None and s.correctness is not None
    ]
    return ArmResult(
        name=name,
        description=description,
        missions=len(samples),
        passes=passes,
        success_rate=passes / len(samples) if samples else 0.0,
        completion_rate=(
            sum(s.status == "COMPLETED" for s in samples) / len(samples) if samples else 0.0
        ),
        mean_correctness=_mean([s.correctness for s in samples]),
        mean_evidence_quality=_mean([s.evidence_quality for s in samples]),
        tool_calls_per_mission=sum(s.tool_calls for s in samples) / len(samples) if samples else 0,
        mission_latency_ms=percentiles([s.latency_ms for s in samples]),
        model_latency_ms=percentiles([v for s in samples for v in s.model_latencies_ms]),
        input_tokens=sum(s.input_tokens for s in samples),
        output_tokens=sum(s.output_tokens for s in samples),
        cache_read_tokens=read,
        cache_write_tokens=sum(s.cache_write_tokens for s in samples),
        cache_hit_rate=read / prompt if prompt else 0.0,
        cache_savings_micro_usd=sum(s.cache_savings_micro_usd for s in samples),
        cost_micro_usd=cost,
        cost_per_success_micro_usd=cost // passes if passes else None,
        expected_calibration_error=expected_calibration_error(calibration),
        recovery_rate=(
            sum(s.status == "COMPLETED" for s in crashed) / len(crashed) if crashed else None
        ),
        duplicate_rate=duplicate_rate,
        failures_by_kind=dict(sorted(Counter(k for s in samples for k in s.failure_kinds).items())),
    )


def duplicate_rate(effects_per_key: dict[str, int]) -> float | None:
    """Share of idempotency keys whose side effect happened more than once."""
    if not effects_per_key:
        return None
    return sum(count > 1 for count in effects_per_key.values()) / len(effects_per_key)
