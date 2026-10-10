"""The Phase 11 harness on a real database: each mechanism moves its metric the way the
mechanism guarantees (ADR 0013). One repeat keeps it fast; `make benchmark` runs more."""

import json
from typing import Any

import pytest

from forge.benchmarking.harness import Config, Harness, Results
from forge.benchmarking.report import effects, to_json, to_markdown
from forge.infrastructure import postgres
from forge.infrastructure.clock import SystemClock
from forge.infrastructure.store import PostgresUnitOfWork
from tests.databases import scratch_database
from tests.integration.conftest import UowFactory

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


@pytest.fixture
async def results(uow: UowFactory) -> Results:
    return await Harness(uow, SystemClock(), Config(repeats=1, lease_seconds=1.0)).run()


async def test_every_mechanism_moves_its_metric_in_the_guaranteed_direction(
    results: Results,
) -> None:
    arm = results.arm
    measured = effects(results)

    # Learning: the same agent, after a validated promotion, passes more cases.
    assert results.learning.status == "PROMOTED"
    assert arm("after_learning").success_rate > arm("single_agent").success_rate
    # Routing: measured fitness picks the able agent; round-robin concludes with both.
    assert arm("forge_routing").success_rate > arm("naive_multi_agent").success_rate
    # Context layout and provider caching: same work, cheaper, with cache reads.
    assert arm("forge_routing").cache_hit_rate > arm("naive_context").cache_hit_rate
    assert arm("forge_routing").cost_micro_usd < arm("naive_context").cost_micro_usd
    assert arm("forge_routing").cost_micro_usd < arm("no_caching").cost_micro_usd
    assert arm("no_caching").cache_read_tokens == 0
    # Durability: crashes happened, and no side effect ever happened twice.
    assert results.chaos_crashes > 0
    assert arm("chaos_side_effects").duplicate_rate == 0.0
    assert set(arm("chaos_side_effects").failures_by_kind) <= {
        "TOOL_OUTCOME_UNKNOWN",
        "RECOVERY_EXHAUSTED",
    }
    # Every arm ran every case and was scored against ground truth.
    assert all(a.missions == 3 for a in results.arms)
    assert all(a.mean_correctness is not None for a in results.arms)
    assert measured["routing"]["success_rate_delta"] > 0


async def test_reports_carry_every_arm_and_the_environment(results: Results) -> None:
    environment = {"commit": "abc", "generated_at": "now", "python": "3", "platform": "p"}

    rendered = to_markdown(results, {**environment, "postgres": "17"})
    payload = to_json(results, environment)

    for name in ("single_agent", "naive_multi_agent", "forge_routing", "chaos_side_effects"):
        assert f"`{name}`" in rendered
        assert f'"name": "{name}"' in payload
    assert "Commit `abc`" in rendered


async def test_two_runs_with_the_same_seed_produce_the_same_numbers(
    server_url: str, template_database: str
) -> None:
    """Everything but wall-clock latency and timestamps reproduces exactly (ADR 0013)."""

    async def once() -> dict[str, Any]:
        with scratch_database(server_url, template=template_database) as url:
            engine = postgres.create_engine(url)
            try:
                results = await Harness(
                    lambda: PostgresUnitOfWork(engine),
                    SystemClock(),
                    Config(repeats=1, lease_seconds=1.0),
                ).run()
            finally:
                await engine.dispose()
        stripped: dict[str, Any] = _without_timing(json.loads(to_json(results, {})))
        return stripped

    assert await once() == await once()


def _without_timing(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: _without_timing(v)
            for k, v in value.items()
            if "latency" not in k and k not in {"cycle_id", "generated_at"}
        }
    if isinstance(value, list):
        return [_without_timing(v) for v in value]
    return value
