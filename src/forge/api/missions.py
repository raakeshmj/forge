"""Mission endpoints: submit, inspect, cancel, and read the event history."""

from uuid import UUID

from fastapi import APIRouter, Request, status

from forge.api.dependencies import clock, uow_factory
from forge.api.schemas import EventView, MissionCreate, MissionView
from forge.domain.mission import Budget, ExecutionPolicy, RiskPolicy
from forge.domain.plan import PlannedTask
from forge.observability.logs import correlation_id
from forge.observability.tracing import record_finished_mission
from forge.orchestration.missions import (
    MissionRequest,
    cancel_mission,
    cancel_task,
    submit_mission,
)

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
            planner_agent_id=body.planner_agent_id,
            tasks=None
            if body.tasks is None
            else tuple(
                PlannedTask(
                    key=spec.key,
                    objective=spec.objective,
                    depends_on=tuple(spec.depends_on),
                    required_capabilities=tuple(spec.required_capabilities),
                    agent_id=spec.agent_id,
                    allowed_tools=None if spec.allowed_tools is None else tuple(spec.allowed_tools),
                    max_attempts=spec.max_attempts,
                )
                for spec in body.tasks
            ),
            tool_scope=None if body.tool_scope is None else tuple(body.tool_scope),
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


@router.post("/{mission_id}/tasks/{task_id}/cancel")
async def cancel_one_task(mission_id: UUID, task_id: UUID, request: Request) -> MissionView:
    mission, tasks = await cancel_task(uow_factory(request), clock(request), mission_id, task_id)
    if mission.status.is_terminal:
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
