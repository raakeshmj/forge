"""Capability graph and agent capability views."""

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field

from forge.api.dependencies import clock, uow_factory
from forge.capabilities import engine
from forge.domain.capability import Capability, Competency, Reliability

router = APIRouter(tags=["capabilities"])


class CapabilityIn(BaseModel):
    name: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9_.-]*$")
    description: str = Field(default="", max_length=2_000)
    parent: str | None = None
    prerequisites: list[str] = Field(default_factory=list, max_length=20)
    related: list[str] = Field(default_factory=list, max_length=20)
    required_tools: list[str] = Field(default_factory=list, max_length=20)


class CompetencyView(BaseModel):
    capability: str
    estimated_score: float
    uncertainty: float
    sample_count: int
    effective_samples: float
    recent_trend: float | None
    calibration: float | None
    last_evaluated: datetime | None
    supporting_evaluations: list[UUID]

    @classmethod
    def of(cls, c: Competency) -> "CompetencyView":
        return cls(
            capability=c.capability,
            estimated_score=round(c.estimated_score, 4),
            uncertainty=round(c.uncertainty, 4),
            sample_count=c.sample_count,
            effective_samples=c.effective_samples,
            recent_trend=c.recent_trend,
            calibration=c.calibration,
            last_evaluated=c.last_evaluated,
            supporting_evaluations=list(c.supporting_evaluations),
        )


class ReliabilityView(BaseModel):
    estimate: float
    uncertainty: float
    successes: int
    failures: int
    mean_cost_micro_usd: float | None
    mean_latency_ms: float | None

    @classmethod
    def of(cls, r: Reliability) -> "ReliabilityView":
        return cls(
            estimate=round(r.estimate, 4),
            uncertainty=round(r.uncertainty, 4),
            successes=r.successes,
            failures=r.failures,
            mean_cost_micro_usd=r.mean_cost_micro_usd,
            mean_latency_ms=r.mean_latency_ms,
        )


class AgentSummary(BaseModel):
    id: UUID
    name: str
    version: int
    version_id: UUID
    role: str
    model: str
    fallback_models: list[str]
    tools: list[str]
    capabilities: list[str]
    created_at: datetime


class AgentDetail(AgentSummary):
    instructions: str
    competencies: list[CompetencyView]
    reliability: ReliabilityView


@router.get("/capabilities")
async def list_capabilities(request: Request) -> list[dict[str, Any]]:
    await engine.register_builtin(uow_factory(request), clock(request))
    async with uow_factory(request)() as uow:
        graph = await uow.capabilities.graph()
    return [
        {
            "name": c.name,
            "description": c.description,
            "parent": c.parent,
            "prerequisites": list(c.prerequisites),
            "related": list(c.related),
            "required_tools": list(c.required_tools),
        }
        for c in graph.capabilities.values()
    ]


@router.post("/capabilities", status_code=status.HTTP_201_CREATED)
async def save_capability(body: CapabilityIn, request: Request) -> dict[str, str]:
    capability = Capability(
        name=body.name,
        description=body.description,
        parent=body.parent,
        prerequisites=tuple(body.prerequisites),
        related=tuple(body.related),
        required_tools=tuple(body.required_tools),
    )
    async with uow_factory(request)() as uow:
        await uow.capabilities.save(capability, clock(request).now())
        (await uow.capabilities.graph()).ancestors(capability.name)  # rejects parent cycles
    return {"name": capability.name}


@router.get("/agents")
async def list_agents(request: Request) -> list[AgentSummary]:
    async with uow_factory(request)() as uow:
        return [_summary(agent, version) for agent, version in await uow.agents.list_current()]


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: UUID, request: Request) -> AgentDetail:
    async with uow_factory(request)() as uow:
        agent = await uow.agents.get(agent_id)
        version = await uow.agents.get_current_version(agent_id)
        competencies = await uow.competencies.for_agents([agent_id])
        reliability = (await uow.executions.reliability([agent_id])).get(agent_id, Reliability())
    return AgentDetail(
        **_summary(agent, version).model_dump(),
        instructions=version.instructions,
        competencies=[CompetencyView.of(c) for c in competencies],
        reliability=ReliabilityView.of(reliability),
    )


def _summary(agent: Any, version: Any) -> AgentSummary:
    return AgentSummary(
        id=agent.id,
        name=agent.name,
        version=version.version,
        version_id=version.id,
        role=version.role,
        model=f"{version.model.provider}/{version.model.name}",
        fallback_models=[f"{m.provider}/{m.name}" for m in version.fallback_models],
        tools=list(version.tools),
        capabilities=list(version.capabilities),
        created_at=agent.created_at,
    )
