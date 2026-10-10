"""Mission endpoints: submit, inspect, cancel, and read the event history."""

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import StreamingResponse

from forge.api.dependencies import clock, uow_factory, wakeup
from forge.api.schemas import EventView, MissionCreate, MissionSummaryView, MissionView
from forge.domain.execution import ExecutionStatus
from forge.domain.mission import Budget, ExecutionPolicy, RiskPolicy
from forge.domain.plan import PlannedTask
from forge.domain.repositories import StoreUnavailableError
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


@router.get("")
async def list_missions(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
) -> list[MissionSummaryView]:
    """Most recent first, with spend and progress (a read model, ADR 0012)."""
    async with uow_factory(request)() as uow:
        summaries = await uow.insights.missions(limit=limit, status=status_filter)
    return [MissionSummaryView.of(m) for m in summaries]


@router.get("/{mission_id}")
async def get_mission(mission_id: UUID, request: Request) -> MissionView:
    return await _view(request, mission_id)


@router.get("/{mission_id}/events/stream")
async def stream_events(mission_id: UUID, request: Request) -> StreamingResponse:
    """Server-Sent Events: every event of the mission once, in order, until it ends.

    Each poll re-reads the mission's events and sends the unseen ones by id, so an event
    committed late by a concurrent transaction is never skipped (ADR 0012).
    """
    async with uow_factory(request)() as uow:
        await uow.missions.get(mission_id)  # 404 before the stream starts
    poll = float(request.app.state.stream_poll_seconds)

    async def frames() -> AsyncIterator[str]:
        sent: set[UUID] = set()
        while not await request.is_disconnected():
            try:
                async with uow_factory(request)() as uow:
                    mission = await uow.missions.get(mission_id)
                    records = await uow.events.list_for_mission(mission_id)
            except StoreUnavailableError:  # recovery: end; EventSource reconnects by itself
                return
            for record in records:
                if record.id not in sent:
                    sent.add(record.id)
                    data = EventView.of(record).model_dump_json()
                    yield f"id: {record.id}\ndata: {data}\n\n"  # type is in the data
            if mission.status.is_terminal:
                yield "event: end\ndata: {}\n\n"
                return
            yield ": keepalive\n\n"
            await asyncio.sleep(poll)

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{mission_id}/cancel")
async def cancel(mission_id: UUID, request: Request) -> MissionView:
    mission, tasks = await cancel_mission(uow_factory(request), clock(request), mission_id)
    async with uow_factory(request)() as uow:
        spent = await uow.executions.mission_spend(mission.id)
    record_finished_mission(mission, tasks, cost_micro_usd=spent)
    await _wake_unfinished(request, mission_id)
    return await _view(request, mission_id)


@router.post("/{mission_id}/tasks/{task_id}/cancel")
async def cancel_one_task(mission_id: UUID, task_id: UUID, request: Request) -> MissionView:
    mission, tasks = await cancel_task(uow_factory(request), clock(request), mission_id, task_id)
    await _wake_unfinished(request, mission_id)
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


async def _wake_unfinished(request: Request, mission_id: UUID) -> None:
    """Parked or running executions should notice the cancellation now (ADR 0011)."""
    async with uow_factory(request)() as uow:
        executions = await uow.executions.list_for_mission(mission_id)
    live = (ExecutionStatus.RUNNING, ExecutionStatus.WAITING_APPROVAL)
    await wakeup(request).wake([e for e in executions if e.status in live])
