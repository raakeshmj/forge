"""Mission endpoints: submit, inspect, cancel, and read the event history."""

from uuid import UUID

from fastapi import APIRouter, Request, status

from forge.api.dependencies import clock, uow_factory
from forge.api.schemas import EventView, MissionCreate, MissionView
from forge.domain.mission import Budget, ExecutionPolicy, RiskPolicy
from forge.observability.logs import correlation_id
from forge.observability.tracing import record_finished_mission
from forge.orchestration.missions import MissionRequest, cancel_mission, submit_mission

router = APIRouter(prefix="/missions", tags=["missions"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_mission(body: MissionCreate, request: Request) -> MissionView:
    policy = body.execution_policy
    mission = await submit_mission(
        uow_factory(request),
        clock(request),
        MissionRequest(
            objective=body.objective,
            agent_id=body.agent_id,
            budget=Budget(body.budget_micro_usd),
            deadline=body.deadline,
            constraints=tuple(body.constraints),
            success_criteria=tuple(body.success_criteria),
            required_capabilities=tuple(body.required_capabilities),
            allowed_tools=body.allowed_tools,
            risk_policy=RiskPolicy(body.max_risk),
            execution_policy=ExecutionPolicy(
                max_steps=policy.max_steps,
                model_timeout_seconds=policy.model_timeout_seconds,
                model_max_attempts=policy.model_max_attempts,
            ),
        ),
        correlation_id=correlation_id.get() or str(UUID(int=0)),
    )
    return await _view(request, mission.id)


@router.get("/{mission_id}")
async def get_mission(mission_id: UUID, request: Request) -> MissionView:
    return await _view(request, mission_id)


@router.post("/{mission_id}/cancel")
async def cancel(mission_id: UUID, request: Request) -> MissionView:
    mission, tasks = await cancel_mission(uow_factory(request), clock(request), mission_id)
    async with uow_factory(request)() as uow:
        spent = await uow.executions.mission_spend(mission.id)
    record_finished_mission(mission, tasks, cost_micro_usd=spent)
    return await _view(request, mission_id)


@router.get("/{mission_id}/events")
async def list_events(mission_id: UUID, request: Request) -> list[EventView]:
    async with uow_factory(request)() as uow:
        await uow.missions.get(mission_id)
        return [EventView.of(record) for record in await uow.events.list_for_mission(mission_id)]


async def _view(request: Request, mission_id: UUID) -> MissionView:
    async with uow_factory(request)() as uow:
        mission = await uow.missions.get(mission_id)
        tasks = await uow.tasks.list_for_mission(mission_id)
        executions = await uow.executions.list_for_mission(mission_id)
    return MissionView.of(mission, tasks, executions)
