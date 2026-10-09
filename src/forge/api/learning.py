"""Learning endpoints: start cycles, inspect them, promote and roll back skills (ADR 0009)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from forge.api.dependencies import clock, operator, uow_factory
from forge.domain.skill import LearningCycle, SkillVersion
from forge.learning import cycle
from forge.learning.generation import DeterministicSkillGenerator

router = APIRouter(tags=["learning"])


class StartCycle(BaseModel):
    agent_id: UUID
    capability: str = Field(min_length=1, max_length=100)


class RollbackRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class CycleView(BaseModel):
    id: UUID
    agent_id: UUID
    capability: str
    status: str
    analysis: dict[str, Any]
    gap: dict[str, Any]
    skill: str | None
    dataset: str | None
    baseline_version_id: UUID | None
    candidate_version_id: UUID | None
    baseline_run_id: UUID | None
    candidate_run_id: UUID | None
    learning_delta: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, c: LearningCycle) -> "CycleView":
        return cls(
            id=c.id,
            agent_id=c.agent_id,
            capability=c.capability,
            status=c.status.value,
            analysis=dict(c.analysis),
            gap=dict(c.gap),
            skill=None if c.skill is None else f"{c.skill.name}@{c.skill.version}",
            dataset=c.dataset,
            baseline_version_id=c.baseline_version_id,
            candidate_version_id=c.candidate_version_id,
            baseline_run_id=c.baseline_run_id,
            candidate_run_id=c.candidate_run_id,
            learning_delta=dict(c.delta),
            created_at=c.created_at,
            updated_at=c.updated_at,
        )


class SkillView(BaseModel):
    name: str
    version: int
    capability: str
    status: str
    instructions: str
    provenance: dict[str, Any]
    validation: dict[str, Any]
    created_at: datetime

    @classmethod
    def of(cls, s: SkillVersion) -> "SkillView":
        return cls(
            name=s.name,
            version=s.version,
            capability=s.capability,
            status=s.status.value,
            instructions=s.instructions,
            provenance=dict(s.provenance),
            validation=dict(s.validation),
            created_at=s.created_at,
        )


@router.post("/learning/cycles", status_code=status.HTTP_201_CREATED)
async def start_cycle(body: StartCycle, request: Request) -> CycleView:
    async with uow_factory(request)() as uow:
        await uow.agents.get(body.agent_id)
        known_tools = {tool.name for tool in await uow.tool_registry.list()}
    started = await cycle.start(
        uow_factory(request),
        clock(request),
        body.agent_id,
        body.capability,
        generator=DeterministicSkillGenerator(),
        known_tools=known_tools,
    )
    return CycleView.of(started)


@router.get("/learning/cycles")
async def list_cycles(request: Request, agent_id: UUID | None = None) -> list[CycleView]:
    async with uow_factory(request)() as uow:
        return [CycleView.of(c) for c in await uow.learning.list_cycles(agent_id)]


@router.get("/learning/cycles/{cycle_id}")
async def get_cycle(cycle_id: UUID, request: Request) -> CycleView:
    async with uow_factory(request)() as uow:
        return CycleView.of(await uow.learning.get(cycle_id))


@router.post("/learning/cycles/{cycle_id}/promote")
async def promote(cycle_id: UUID, request: Request, by: str = Depends(operator)) -> CycleView:
    return CycleView.of(
        await cycle.promote(uow_factory(request), clock(request), cycle_id, operator=by)
    )


@router.post("/learning/cycles/{cycle_id}/rollback")
async def rollback(
    cycle_id: UUID, body: RollbackRequest, request: Request, by: str = Depends(operator)
) -> CycleView:
    return CycleView.of(
        await cycle.rollback(
            uow_factory(request), clock(request), cycle_id, reason=body.reason, actor=by
        )
    )


@router.get("/skills")
async def list_skills(request: Request) -> list[SkillView]:
    async with uow_factory(request)() as uow:
        return [SkillView.of(s) for s in await uow.skills.list_all()]
