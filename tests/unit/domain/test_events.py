from uuid import uuid4

import pytest

from forge.domain.cost import Usage
from forge.domain.events import ModelCalled, ToolCalled


def test_payload_excludes_envelope_fields_and_serialises_values() -> None:
    execution_id, mission_id = uuid4(), uuid4()
    event = ModelCalled(
        aggregate_id=execution_id,
        mission_id=mission_id,
        turn=0,
        provider="demo",
        model="demo-investigator-1",
        attempts=2,
        stop_reason="tool_use",
        usage=Usage(10, 5, 0, 0),
        cost_micro_usd=42,
        latency_ms=12,
        cache_savings_micro_usd=0,
        context_tokens=300,
        context_blocks=("system.platform@platform-1",),
        elided=(),
        cache_breakpoints=(),
    )

    assert event.event_type == "model.called"
    assert event.aggregate_type == "execution"
    assert event.payload() == {
        "turn": 0,
        "provider": "demo",
        "model": "demo-investigator-1",
        "attempts": 2,
        "stop_reason": "tool_use",
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
        "cost_micro_usd": 42,
        "latency_ms": 12,
        "cache_savings_micro_usd": 0,
        "context_tokens": 300,
        "context_blocks": ["system.platform@platform-1"],
        "elided": [],
        "cache_breakpoints": [],
    }


def test_events_are_immutable() -> None:
    event = ToolCalled(
        aggregate_id=uuid4(),
        mission_id=uuid4(),
        turn=0,
        tool_use_id="t1",
        tool="metrics.read",
        tool_version="1",
        idempotency_key="k",
        invocation=1,
    )

    with pytest.raises(AttributeError):
        event.tool = "other"  # type: ignore[misc]
