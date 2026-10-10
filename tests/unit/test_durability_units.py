"""Small pure pieces of the Phase 9 durability design (ADR 0011)."""

from forge.domain.repositories import outage_delay
from forge.infrastructure.temporal.workflows import workflow_id


def test_outage_backoff_grows_exponentially_and_is_capped() -> None:
    delays = [outage_delay(n) for n in range(1, 10)]

    assert delays[:4] == [0.5, 1.0, 2.0, 4.0]
    assert max(delays) == 30.0
    assert delays == sorted(delays)


def test_workflow_ids_are_deterministic_per_task_attempt() -> None:
    assert workflow_id("t", 1) == workflow_id("t", 1) == "forge-task-t-1"
    assert workflow_id("t", 2) != workflow_id("t", 1)
