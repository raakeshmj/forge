"""OpenTelemetry tracing with a hierarchy that survives worker crashes (ADR 0003).

A mission's trace id and its tasks' span ids are derived from their UUIDs, so an
execution resumed by a different process still parents its spans under the same
task span, inside the same trace. Mission and task spans are emitted once, by
whichever transaction made them terminal, with their recorded start times.

Spans carry identifiers, counts, costs, and statuses only, never prompt or tool
content (CLAUDE.md §15).
"""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from uuid import UUID

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SpanExporter,
)
from opentelemetry.sdk.trace.id_generator import RandomIdGenerator
from opentelemetry.trace import NonRecordingSpan, Span, SpanContext, Status, StatusCode, TraceFlags

from forge.domain.execution import Execution
from forge.domain.mission import Mission, MissionStatus
from forge.domain.task import Task

_SPAN_ID_MASK = (1 << 64) - 1
_forced_trace_id: ContextVar[int | None] = ContextVar("forge_forced_trace_id", default=None)
_forced_span_id: ContextVar[int | None] = ContextVar("forge_forced_span_id", default=None)


class DeterministicIdGenerator(RandomIdGenerator):
    """Random ids, except while `_forced_ids` pins them for one span start."""

    def generate_trace_id(self) -> int:
        forced = _forced_trace_id.get()
        return super().generate_trace_id() if forced is None else forced

    def generate_span_id(self) -> int:
        forced = _forced_span_id.get()
        return super().generate_span_id() if forced is None else forced


def configure_tracing(exporter: str, *, service_name: str) -> TracerProvider | None:
    """Install the global tracer provider. `none` leaves the no-op provider in place."""
    if exporter == "none":
        return None
    span_exporter: SpanExporter
    if exporter == "console":
        span_exporter = ConsoleSpanExporter()
    else:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        span_exporter = OTLPSpanExporter()  # endpoint from standard OTEL_EXPORTER_OTLP_* vars
    provider = new_tracer_provider(service_name)
    provider.add_span_processor(BatchSpanProcessor(span_exporter))
    trace.set_tracer_provider(provider)
    return provider


def new_tracer_provider(service_name: str) -> TracerProvider:
    return TracerProvider(
        resource=Resource.create({"service.name": service_name}),
        id_generator=DeterministicIdGenerator(),
    )


def tracer() -> trace.Tracer:
    return trace.get_tracer("forge")


def mission_trace_id(mission_id: UUID) -> int:
    return mission_id.int or 1


def _span_id(entity_id: UUID) -> int:
    return (entity_id.int & _SPAN_ID_MASK) or 1


def _remote_parent(trace_id: int, span_id: int) -> trace.Span:
    return NonRecordingSpan(
        SpanContext(
            trace_id=trace_id,
            span_id=span_id,
            is_remote=True,
            trace_flags=TraceFlags(TraceFlags.SAMPLED),
        )
    )


@contextmanager
def _forced_ids(trace_id: int, span_id: int) -> Iterator[None]:
    trace_token = _forced_trace_id.set(trace_id)
    span_token = _forced_span_id.set(span_id)
    try:
        yield
    finally:
        _forced_span_id.reset(span_token)
        _forced_trace_id.reset(trace_token)


def _ns(moment: datetime) -> int:
    return int(moment.timestamp() * 1_000_000_000)


@contextmanager
def agent_span(execution: Execution, *, worker_id: str) -> Iterator[Span]:
    """One span per execution attempt, parented to its task span across process restarts."""
    parent = _remote_parent(mission_trace_id(execution.mission_id), _span_id(execution.task_id))
    with tracer().start_as_current_span(
        "forge.agent",
        context=trace.set_span_in_context(parent),
        attributes={
            "forge.mission_id": str(execution.mission_id),
            "forge.task_id": str(execution.task_id),
            "forge.execution_id": str(execution.id),
            "forge.agent_id": str(execution.agent_id),
            "forge.agent_version": str(execution.agent_version_id),
            "forge.model": f"{execution.model.provider}/{execution.model.name}",
            "forge.attempt": execution.attempt,
            "forge.worker_id": worker_id,
        },
    ) as span:
        yield span


def finish_agent_span(span: Span, execution: Execution) -> None:
    span.set_attribute("forge.status", execution.status.value)
    span.set_attribute("forge.cost_micro_usd", execution.cost_micro_usd)
    span.set_attribute("forge.total_tokens", execution.usage.total_tokens)
    if execution.failure_kind is not None:
        span.set_attribute("forge.outcome", execution.failure_kind.value)
        span.set_status(Status(StatusCode.ERROR, execution.failure_kind.value))


def record_finished_mission(
    mission: Mission,
    tasks: Sequence[Task],
    *,
    cost_micro_usd: int,
    executions: Sequence[Execution] = (),
) -> None:
    """Emit the task spans and the mission span once the mission is terminal."""
    if not mission.status.is_terminal:
        return
    trace_id = mission_trace_id(mission.id)
    mission_span_id = _span_id(mission.id)
    by_task = {execution.task_id: execution for execution in executions}
    usage_tokens = sum(execution.usage.total_tokens for execution in executions)
    cached_tokens = sum(execution.usage.cache_read_input_tokens for execution in executions)

    for task in tasks:
        execution = by_task.get(task.id)
        with _forced_ids(trace_id, _span_id(task.id)):
            span = tracer().start_span(
                "forge.task",
                context=trace.set_span_in_context(_remote_parent(trace_id, mission_span_id)),
                start_time=_ns(task.created_at),
                attributes={
                    "forge.task_id": str(task.id),
                    "forge.mission_id": str(mission.id),
                    "forge.status": task.status.value,
                    "forge.selected_agent": str(task.agent_id),
                    "forge.cost_micro_usd": 0 if execution is None else execution.cost_micro_usd,
                },
            )
        span.end(end_time=_ns(task.updated_at))

    with _forced_ids(trace_id, mission_span_id):
        span = tracer().start_span(
            "forge.mission",
            context=trace.set_span_in_context(trace.INVALID_SPAN),
            start_time=_ns(mission.created_at),
            attributes={
                "forge.mission_id": str(mission.id),
                "forge.mission_version": mission.version,
                "forge.status": mission.status.value,
                "forge.task_count": len(tasks),
                "forge.agent_count": len({task.agent_id for task in tasks}),
                "forge.total_cost_micro_usd": cost_micro_usd,
                "forge.total_tokens": usage_tokens,
                "forge.cached_tokens": cached_tokens,
            },
        )
    if mission.status is not MissionStatus.COMPLETED:
        span.set_status(Status(StatusCode.ERROR, mission.status.value))
    span.end(end_time=_ns(mission.updated_at))
