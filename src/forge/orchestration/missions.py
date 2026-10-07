"""Mission service: submission, cancellation, and the lifecycle transitions the runtime requests.

Only this module writes missions and tasks. The runtime calls `start_task` and
`finish_task` inside its own unit of work so execution state, task state, and
mission state commit atomically (ADR 0003).
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from forge.domain.common import DomainError
from forge.domain.events import (
    DomainEvent,
    MissionCancelled,
    MissionCompleted,
    MissionCreated,
    MissionFailed,
    MissionPlanned,
    MissionStarted,
    TaskCancelled,
    TaskCompleted,
    TaskCreated,
    TaskFailed,
    TaskStarted,
)
from forge.domain.execution import Execution, ExecutionStatus
from forge.domain.mission import Budget, ExecutionPolicy, Mission, MissionStatus, RiskPolicy
from forge.domain.repositories import Clock, UnitOfWork, UnitOfWorkFactory
from forge.domain.task import Task, TaskStatus


class MissionAlreadyFinishedError(DomainError):
    def __init__(self, mission_id: UUID, status: MissionStatus) -> None:
        super().__init__(f"mission {mission_id} is already {status}")
        self.mission_id = mission_id
        self.status = status


@dataclass(frozen=True, slots=True)
class MissionRequest:
    objective: str
    agent_id: UUID
    budget: Budget
    deadline: datetime | None = None
    constraints: tuple[str, ...] = ()
    success_criteria: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    allowed_tools: Sequence[str] | None = None  # None: everything the agent version may use
    risk_policy: RiskPolicy = field(default_factory=RiskPolicy)
    execution_policy: ExecutionPolicy = field(default_factory=ExecutionPolicy)


async def submit_mission(
    uow_factory: UnitOfWorkFactory,
    clock: Clock,
    request: MissionRequest,
    *,
    correlation_id: str,
) -> Mission:
    """Create the mission and its single task, ready for a worker to claim."""
    now = clock.now()
    async with uow_factory() as uow:
        version = await uow.agents.get_current_version(request.agent_id)
        mission = Mission(
            id=uuid4(),
            version=1,
            objective=request.objective,
            constraints=request.constraints,
            success_criteria=request.success_criteria,
            required_capabilities=request.required_capabilities,
            budget=request.budget,
            deadline=request.deadline,
            risk_policy=request.risk_policy,
            execution_policy=request.execution_policy,
            status=MissionStatus.CREATED,
            correlation_id=correlation_id,
            created_at=now,
            updated_at=now,
        )
        allowed = version.tools if request.allowed_tools is None else request.allowed_tools
        task = Task(
            id=uuid4(),
            version=1,
            mission_id=mission.id,
            objective=mission.objective,
            agent_id=request.agent_id,
            allowed_tools=tuple(sorted(set(allowed))),
            status=TaskStatus.PENDING,
            created_at=now,
            updated_at=now,
        )
        # Phase 1 planning is trivial: the whole objective becomes one task.
        mission = mission.transition(MissionStatus.PLANNING, now).transition(
            MissionStatus.READY, now
        )
        task = task.transition(TaskStatus.READY, now)

        await uow.missions.add(mission)
        await uow.tasks.add(task)
        emit = _Emitter(uow, correlation_id)
        await emit(
            MissionCreated(
                aggregate_id=mission.id,
                mission_id=mission.id,
                agent_id=request.agent_id,
                budget_micro_usd=mission.budget.limit_micro_usd,
                max_risk=mission.risk_policy.max_risk.value,
                deadline=mission.deadline,
            )
        )
        await emit(
            TaskCreated(
                aggregate_id=task.id,
                mission_id=mission.id,
                agent_id=task.agent_id,
                allowed_tools=task.allowed_tools,
            )
        )
        await emit(MissionPlanned(aggregate_id=mission.id, mission_id=mission.id, task_count=1))
    return mission


async def cancel_mission(
    uow_factory: UnitOfWorkFactory, clock: Clock, mission_id: UUID
) -> tuple[Mission, list[Task]]:
    """Cancel the mission and its unfinished tasks. Running executions stop at their next step."""
    now = clock.now()
    async with uow_factory() as uow:
        mission = await uow.missions.get(mission_id, for_update=True)
        if mission.status.is_terminal:
            raise MissionAlreadyFinishedError(mission.id, mission.status)
        emit = _Emitter(uow, mission.correlation_id)
        mission = await uow.missions.update(mission.transition(MissionStatus.CANCELLED, now))
        await emit(MissionCancelled(aggregate_id=mission.id, mission_id=mission.id))
        tasks = []
        for task in await uow.tasks.list_for_mission(mission.id):
            if not task.status.is_terminal:
                task = await uow.tasks.update(task.transition(TaskStatus.CANCELLED, now))
                await emit(TaskCancelled(aggregate_id=task.id, mission_id=mission.id))
            tasks.append(task)
    return mission, tasks


async def start_task(uow: UnitOfWork, task: Task, execution_id: UUID, now: datetime) -> None:
    """A worker claimed `task` (already locked by the caller): mark it, and its mission, running."""
    mission = await uow.missions.get(task.mission_id, for_update=True)
    emit = _Emitter(uow, mission.correlation_id)
    await uow.tasks.update(task.transition(TaskStatus.RUNNING, now))
    await emit(TaskStarted(aggregate_id=task.id, mission_id=mission.id, execution_id=execution_id))
    if mission.status is MissionStatus.READY:
        await uow.missions.update(mission.transition(MissionStatus.RUNNING, now))
        await emit(MissionStarted(aggregate_id=mission.id, mission_id=mission.id))


async def lock_mission(uow: UnitOfWork, mission_id: UUID) -> Mission:
    """Read the mission under its row lock, serialising against cancellation."""
    return await uow.missions.get(mission_id, for_update=True)


async def finish_task(
    uow: UnitOfWork, mission: Mission, execution: Execution, now: datetime
) -> Mission:
    """Record a COMPLETED or FAILED execution on its task, then settle the mission.

    The caller holds the mission lock (`lock_mission`) and has checked the mission
    was not cancelled. The mission completes when every task has completed and
    fails as soon as any task fails.
    """
    emit = _Emitter(uow, mission.correlation_id)
    task = await uow.tasks.get(execution.task_id, for_update=True)
    if execution.status is ExecutionStatus.COMPLETED:
        await uow.tasks.update(task.transition(TaskStatus.COMPLETED, now))
        await emit(TaskCompleted(aggregate_id=task.id, mission_id=mission.id))
    elif execution.status is ExecutionStatus.FAILED and execution.failure_kind is not None:
        await uow.tasks.update(task.transition(TaskStatus.FAILED, now))
        await emit(
            TaskFailed(
                aggregate_id=task.id,
                mission_id=mission.id,
                failure_kind=execution.failure_kind.value,
            )
        )
    else:
        raise DomainError(f"execution {execution.id} is {execution.status}, not finished")

    tasks = await uow.tasks.list_for_mission(mission.id)
    spent = await uow.executions.mission_spend(mission.id)
    if any(task.status is TaskStatus.FAILED for task in tasks):
        mission = await uow.missions.update(mission.transition(MissionStatus.FAILED, now))
        failure = execution.failure_kind.value if execution.failure_kind else "TASK_FAILED"
        await emit(
            MissionFailed(
                aggregate_id=mission.id,
                mission_id=mission.id,
                failure_kind=failure,
                cost_micro_usd=spent,
            )
        )
    elif all(task.status is TaskStatus.COMPLETED for task in tasks):
        mission = await uow.missions.update(mission.transition(MissionStatus.COMPLETED, now))
        await emit(
            MissionCompleted(aggregate_id=mission.id, mission_id=mission.id, cost_micro_usd=spent)
        )
    return mission


class _Emitter:
    def __init__(self, uow: UnitOfWork, correlation_id: str | None) -> None:
        self._uow = uow
        self._correlation_id = correlation_id

    async def __call__(self, event: DomainEvent) -> None:
        await self._uow.events.append(event, correlation_id=self._correlation_id)
