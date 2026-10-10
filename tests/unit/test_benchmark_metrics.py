"""Benchmark metric definitions (ADR 0013)."""

from dataclasses import replace
from uuid import uuid4

import pytest

from forge.benchmarking.metrics import MissionSample, duplicate_rate, percentile, summarize


def sample(**overrides: object) -> MissionSample:
    base = MissionSample(
        mission_id=uuid4(),
        case="c",
        status="COMPLETED",
        passed=True,
        correctness=1.0,
        evidence_quality=1.0,
        stated_confidence=0.8,
        tool_calls=1,
        latency_ms=100.0,
        model_latencies_ms=(10.0, 20.0),
        input_tokens=100,
        output_tokens=10,
        cache_read_tokens=300,
        cache_write_tokens=100,
        cost_micro_usd=1_000,
        cache_savings_micro_usd=50,
        crashed=False,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def test_percentile_interpolates_between_closest_ranks() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert percentile(values, 0.5) == 3.0
    assert percentile(values, 0.95) == pytest.approx(4.8)
    assert percentile([7.0], 0.99) == 7.0
    assert percentile([], 0.5) is None
    with pytest.raises(ValueError, match="within"):
        percentile(values, 1.5)


def test_summary_counts_success_cost_per_success_and_cache_efficiency() -> None:
    result = summarize(
        "arm",
        "d",
        [sample(), sample(passed=False, correctness=0.0, cost_micro_usd=3_000), sample()],
    )

    assert (result.missions, result.passes) == (3, 2)
    assert result.success_rate == pytest.approx(2 / 3)
    assert result.cost_micro_usd == 5_000
    assert result.cost_per_success_micro_usd == 2_500
    assert result.cache_hit_rate == pytest.approx(900 / (300 + 900 + 300))
    assert result.mean_correctness == pytest.approx(2 / 3)
    assert result.model_latency_ms.p50 == 15.0


def test_no_successes_means_no_cost_per_success_rather_than_a_division_by_zero() -> None:
    result = summarize("arm", "d", [sample(passed=False)])
    assert result.cost_per_success_micro_usd is None


def test_recovery_rate_counts_only_missions_that_crashed() -> None:
    result = summarize(
        "arm",
        "d",
        [
            sample(crashed=True),
            sample(crashed=True, status="FAILED", failure_kinds=("RECOVERY_EXHAUSTED",)),
            sample(),
        ],
    )
    assert result.recovery_rate == 0.5
    assert result.failures_by_kind == {"RECOVERY_EXHAUSTED": 1}
    assert summarize("arm", "d", [sample()]).recovery_rate is None


def test_duplicate_rate_is_the_share_of_keys_whose_effect_repeated() -> None:
    assert duplicate_rate({"a": 1, "b": 1, "c": 2, "d": 1}) == 0.25
    assert duplicate_rate({}) is None


def test_unscored_dimensions_are_left_out_of_means() -> None:
    result = summarize("arm", "d", [sample(evidence_quality=None), sample(evidence_quality=0.5)])
    assert result.mean_evidence_quality == 0.5
