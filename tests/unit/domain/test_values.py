from datetime import datetime, timedelta

import pytest

from forge.domain.common import DomainError
from forge.domain.cost import ModelPrice, Usage
from forge.domain.mission import Budget, ExecutionPolicy
from forge.domain.retry import RetryPolicy
from forge.domain.risk import RiskLevel
from forge.domain.tool import Idempotency
from tests import factories
from tests.factories import NOW


def test_cost_is_exact_and_rounds_up() -> None:
    price = ModelPrice(
        input=3_000_000, output=15_000_000, cache_read=300_000, cache_write=3_750_000
    )
    usage = Usage(
        input_tokens=1000,
        output_tokens=200,
        cache_read_input_tokens=5000,
        cache_creation_input_tokens=1,
    )

    # 3000 + 3000 + 1500 + 3.75 micro-USD -> 7503.75 rounds up to 7504
    assert price.cost_of(usage) == 7504


def test_usage_adds_every_field_including_cache_tokens() -> None:
    total = Usage(1, 2, 3, 4) + Usage(10, 20, 30, 40)

    assert total == Usage(11, 22, 33, 44)
    assert total.total_tokens == 110


def test_budget_is_exhausted_at_the_limit() -> None:
    budget = Budget(100)

    assert not budget.is_exhausted_by(99)
    assert budget.is_exhausted_by(100)
    with pytest.raises(DomainError):
        Budget(0)


def test_backoff_doubles_and_is_capped() -> None:
    policy = RetryPolicy(max_attempts=5, initial_backoff_seconds=0.5, max_backoff_seconds=3.0)

    assert [policy.backoff_after(n) for n in (1, 2, 3, 4)] == [0.5, 1.0, 2.0, 3.0]


def test_risk_ordering() -> None:
    assert RiskLevel.MEDIUM.exceeds(RiskLevel.LOW)
    assert not RiskLevel.LOW.exceeds(RiskLevel.LOW)
    assert RiskLevel.CRITICAL.exceeds(RiskLevel.HIGH)


def test_non_idempotent_tools_cannot_declare_retries() -> None:
    with pytest.raises(DomainError, match="non-idempotent"):
        factories.tool_definition(idempotency=Idempotency.NON_IDEMPOTENT, max_attempts=2)


def test_mission_requires_an_objective_and_an_aware_deadline() -> None:
    with pytest.raises(DomainError):
        factories.mission(objective="  ")
    with pytest.raises(DomainError):
        factories.mission(deadline=datetime(2026, 1, 1))


def test_mission_deadline() -> None:
    mission = factories.mission(deadline=NOW + timedelta(seconds=1))

    assert not mission.deadline_passed(NOW)
    assert mission.deadline_passed(NOW + timedelta(seconds=1))
    assert not factories.mission(deadline=None).deadline_passed(NOW)


def test_execution_policy_validates() -> None:
    with pytest.raises(DomainError):
        ExecutionPolicy(max_steps=0)
