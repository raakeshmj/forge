"""Read models for the control plane: observability, costs, agent usage (ADR 0012)."""

from datetime import datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from forge.api.dependencies import clock, uow_factory
from forge.domain.insights import AgentUsage, CostShare, Count, ModelUsage, Overview, ToolUsage

router = APIRouter(tags=["insights"])


class CountView(BaseModel):
    key: str
    count: int

    @classmethod
    def of(cls, c: Count) -> "CountView":
        return cls(key=c.key, count=c.count)


class ModelUsageView(BaseModel):
    provider: str
    model: str
    calls: int
    latency_p50_ms: float
    latency_p95_ms: float
    latency_p99_ms: float
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    cache_hit_rate: float
    cost_micro_usd: int
    cache_savings_micro_usd: int

    @classmethod
    def of(cls, m: ModelUsage) -> "ModelUsageView":
        return cls(
            provider=m.provider,
            model=m.model,
            calls=m.calls,
            latency_p50_ms=m.latency_p50_ms,
            latency_p95_ms=m.latency_p95_ms,
            latency_p99_ms=m.latency_p99_ms,
            input_tokens=m.input_tokens,
            output_tokens=m.output_tokens,
            cache_read_input_tokens=m.cache_read_input_tokens,
            cache_creation_input_tokens=m.cache_creation_input_tokens,
            cache_hit_rate=round(m.cache_hit_rate, 4),
            cost_micro_usd=m.cost_micro_usd,
            cache_savings_micro_usd=m.cache_savings_micro_usd,
        )


class ToolUsageView(BaseModel):
    tool: str
    calls: int
    errors: int
    failures: int
    denials: int
    reinvocations: int

    @classmethod
    def of(cls, t: ToolUsage) -> "ToolUsageView":
        return cls(
            tool=t.tool,
            calls=t.calls,
            errors=t.errors,
            failures=t.failures,
            denials=t.denials,
            reinvocations=t.reinvocations,
        )


class CostShareView(BaseModel):
    key: str
    cost_micro_usd: int
    executions: int

    @classmethod
    def of(cls, c: CostShare) -> "CostShareView":
        return cls(key=c.key, cost_micro_usd=c.cost_micro_usd, executions=c.executions)


class OverviewView(BaseModel):
    since: datetime | None
    total_cost_micro_usd: int
    pending_approvals: int
    missions_by_status: list[CountView]
    executions_by_status: list[CountView]
    failures_by_kind: list[CountView]
    models: list[ModelUsageView]
    tools: list[ToolUsageView]
    cost_by_agent: list[CostShareView]
    cost_by_day: list[CostShareView]

    @classmethod
    def of(cls, o: Overview) -> "OverviewView":
        return cls(
            since=o.since,
            total_cost_micro_usd=o.total_cost_micro_usd,
            pending_approvals=o.pending_approvals,
            missions_by_status=[CountView.of(c) for c in o.missions_by_status],
            executions_by_status=[CountView.of(c) for c in o.executions_by_status],
            failures_by_kind=[CountView.of(c) for c in o.failures_by_kind],
            models=[ModelUsageView.of(m) for m in o.models],
            tools=[ToolUsageView.of(t) for t in o.tools],
            cost_by_agent=[CostShareView.of(c) for c in o.cost_by_agent],
            cost_by_day=[CostShareView.of(c) for c in o.cost_by_day],
        )


class DimensionView(BaseModel):
    dimension: str
    mean: float
    samples: int


class AgentUsageView(BaseModel):
    agent_id: UUID
    executions: int
    cost_micro_usd: int
    models: list[ModelUsageView]
    failures_by_kind: list[CountView]
    dimensions: list[DimensionView]
    findings: list[CountView]

    @classmethod
    def of(cls, u: AgentUsage) -> "AgentUsageView":
        return cls(
            agent_id=u.agent_id,
            executions=u.executions,
            cost_micro_usd=u.cost_micro_usd,
            models=[ModelUsageView.of(m) for m in u.models],
            failures_by_kind=[CountView.of(c) for c in u.failures_by_kind],
            dimensions=[
                DimensionView(dimension=d.dimension, mean=round(d.mean, 4), samples=d.samples)
                for d in u.dimensions
            ],
            findings=[CountView.of(c) for c in u.findings],
        )


@router.get("/insights")
async def overview(
    request: Request, hours: Annotated[float | None, Query(gt=0, le=24 * 366)] = None
) -> OverviewView:
    """Usage, latency, cache efficiency, failures, and cost; all time unless `hours` is set."""
    since = None if hours is None else clock(request).now() - timedelta(hours=hours)
    async with uow_factory(request)() as uow:
        return OverviewView.of(await uow.insights.overview(since))


@router.get("/agents/{agent_id}/usage")
async def agent_usage(agent_id: UUID, request: Request) -> AgentUsageView:
    async with uow_factory(request)() as uow:
        await uow.agents.get(agent_id)
        return AgentUsageView.of(await uow.insights.agent_usage(agent_id))
