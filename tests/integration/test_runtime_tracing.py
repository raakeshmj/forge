"""OpenTelemetry: one trace per mission, Mission > Task > Agent > Model/Tool, across a resume."""

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy.ext.asyncio import AsyncEngine

from forge.observability.tracing import mission_trace_id
from forge.runtime.agent_loop import ExecutionRunner, claim_next
from forge.runtime.worker import Worker
from tests.integration.conftest import UowFactory
from tests.runtime_doubles import (
    CALL_COST,
    RecordingTool,
    ScriptedModel,
    SimulatedCrash,
    answer,
    crash,
    expire_lease,
    runtime,
    submit,
    tool_call,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

MARKER = "objective-text-must-not-leak"


async def test_spans_form_one_hierarchy_across_a_crash_and_carry_no_content(
    uow: UowFactory, engine: AsyncEngine, spans: InMemorySpanExporter
) -> None:
    model = ScriptedModel(tool_call(query=MARKER), crash, answer(MARKER))
    tool = RecordingTool()
    mission_id = await submit(uow)

    first = runtime(uow, model, tool, worker_id="w1")
    claim = await claim_next(first)
    assert claim is not None
    with pytest.raises(SimulatedCrash):
        await ExecutionRunner(first, claim).run()
    await expire_lease(engine, claim.execution.id)
    second = runtime(uow, model, tool, worker_id="w2")
    resumed = await claim_next(second)
    assert resumed is not None
    await Worker(second, poll_interval_seconds=0.01).run_claim(resumed)

    finished = spans.get_finished_spans()
    by_name: dict[str, list] = {}  # type: ignore[type-arg]
    for span in finished:
        by_name.setdefault(span.name, []).append(span)
    trace_id = mission_trace_id(mission_id)
    assert {span.context.trace_id for span in finished} == {trace_id}

    (mission_span,) = by_name["forge.mission"]
    (task_span,) = by_name["forge.task"]
    agent_spans = by_name["forge.agent"]
    assert mission_span.parent is None
    assert task_span.parent.span_id == mission_span.context.span_id
    assert [s.attributes["forge.attempt"] for s in agent_spans] == [1, 2]
    assert {s.parent.span_id for s in agent_spans} == {task_span.context.span_id}
    agent_ids = {s.context.span_id for s in agent_spans}
    for name in ("forge.model.call", "forge.tool.call"):
        assert {s.parent.span_id for s in by_name[name]} <= agent_ids

    assert mission_span.attributes["forge.status"] == "COMPLETED"
    assert mission_span.attributes["forge.total_cost_micro_usd"] == 2 * CALL_COST + 7
    model_ok = [s for s in by_name["forge.model.call"] if s.attributes.get("forge.status") == "ok"]
    assert [s.attributes["forge.cost_micro_usd"] for s in model_ok] == [CALL_COST, CALL_COST]
    assert "forge.cached_tokens" in model_ok[0].attributes
    (tool_span,) = by_name["forge.tool.call"]
    assert tool_span.attributes["forge.permission_result"] == "allowed"

    for span in finished:  # no prompt, argument, or answer text in any attribute
        assert all(MARKER not in str(value) for value in (span.attributes or {}).values())
