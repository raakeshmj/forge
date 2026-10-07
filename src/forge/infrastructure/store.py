"""PostgreSQL implementation of the persistence ports (forge.domain.repositories).

Every lease comparison uses database time so workers never compare clocks.
"""

import json
from dataclasses import replace
from types import TracebackType
from typing import Any, Self
from uuid import UUID, uuid4

from sqlalchemy import Row, and_, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncTransaction

from forge.domain.agent import Agent, AgentVersion, ModelRef
from forge.domain.checkpoint import AgentState, Checkpoint
from forge.domain.common import JSONObject
from forge.domain.cost import Usage
from forge.domain.events import DomainEvent, EventRecord
from forge.domain.execution import Execution, ExecutionStatus, FailureKind
from forge.domain.mission import Budget, ExecutionPolicy, Mission, MissionStatus, RiskPolicy
from forge.domain.repositories import (
    AlreadyExistsError,
    ConcurrentUpdateError,
    LeaseLostError,
    NotFoundError,
)
from forge.domain.risk import RiskLevel
from forge.domain.task import Task, TaskStatus
from forge.domain.tool import InvocationStatus, ToolInvocation
from forge.infrastructure import tables as t


def _lease_until(lease_seconds: float) -> Any:
    return func.now() + func.make_interval(0, 0, 0, 0, 0, 0, lease_seconds)


class PostgresUnitOfWork:
    """One transaction. Commits on clean exit, rolls back on any exception (including cancel)."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def __aenter__(self) -> Self:
        self._connection: AsyncConnection = await self._engine.connect()
        self._transaction: AsyncTransaction = await self._connection.begin()
        connection = self._connection
        self.agents = _Agents(connection)
        self.missions = _Missions(connection)
        self.tasks = _Tasks(connection)
        self.executions = _Executions(connection)
        self.checkpoints = _Checkpoints(connection)
        self.tool_invocations = _ToolInvocations(connection)
        self.events = _Events(connection)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if exc_type is None:
                await self._transaction.commit()
            else:
                await self._transaction.rollback()
        finally:
            await self._connection.close()


class _Repository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def _one(self, statement: Any, entity: str, entity_id: object) -> Row[Any]:
        row = (await self._connection.execute(statement)).first()
        if row is None:
            raise NotFoundError(entity, entity_id)
        return row


# --- Agents ----------------------------------------------------------------------------


class _Agents(_Repository):
    async def add(self, agent: Agent, version: AgentVersion) -> None:
        # A savepoint keeps the caller's transaction usable if the name is taken.
        try:
            async with self._connection.begin_nested():
                await self._connection.execute(
                    insert(t.agents).values(
                        id=agent.id,
                        name=agent.name,
                        current_version=agent.current_version,
                        created_at=agent.created_at,
                    )
                )
        except IntegrityError as exc:
            raise AlreadyExistsError("agent", agent.name) from exc
        await self._connection.execute(
            insert(t.agent_versions).values(
                id=version.id,
                agent_id=version.agent_id,
                version=version.version,
                role=version.role,
                instructions=version.instructions,
                model_provider=version.model.provider,
                model_name=version.model.name,
                tools=list(version.tools),
                created_at=version.created_at,
            )
        )

    async def get(self, agent_id: UUID) -> Agent:
        row = await self._one(select(t.agents).where(t.agents.c.id == agent_id), "agent", agent_id)
        return Agent(
            id=row.id, name=row.name, current_version=row.current_version, created_at=row.created_at
        )

    async def get_version(self, version_id: UUID) -> AgentVersion:
        statement = select(t.agent_versions).where(t.agent_versions.c.id == version_id)
        return _agent_version(await self._one(statement, "agent version", version_id))

    async def get_current_version(self, agent_id: UUID) -> AgentVersion:
        statement = select(t.agent_versions).join(
            t.agents,
            and_(
                t.agents.c.id == t.agent_versions.c.agent_id,
                t.agents.c.current_version == t.agent_versions.c.version,
            ),
        )
        statement = statement.where(t.agents.c.id == agent_id)
        return _agent_version(await self._one(statement, "agent", agent_id))


def _agent_version(row: Row[Any]) -> AgentVersion:
    return AgentVersion(
        id=row.id,
        agent_id=row.agent_id,
        version=row.version,
        role=row.role,
        instructions=row.instructions,
        model=ModelRef(row.model_provider, row.model_name),
        tools=tuple(row.tools),
        created_at=row.created_at,
    )


# --- Missions --------------------------------------------------------------------------


class _Missions(_Repository):
    async def add(self, mission: Mission) -> None:
        policy = mission.execution_policy
        await self._connection.execute(
            insert(t.missions).values(
                id=mission.id,
                version=mission.version,
                objective=mission.objective,
                constraints=list(mission.constraints),
                success_criteria=list(mission.success_criteria),
                required_capabilities=list(mission.required_capabilities),
                budget_micro_usd=mission.budget.limit_micro_usd,
                deadline=mission.deadline,
                max_risk=mission.risk_policy.max_risk.value,
                execution_policy={
                    "max_steps": policy.max_steps,
                    "model_timeout_seconds": policy.model_timeout_seconds,
                    "model_max_attempts": policy.model_max_attempts,
                    "retry_initial_backoff_seconds": policy.retry_initial_backoff_seconds,
                    "retry_max_backoff_seconds": policy.retry_max_backoff_seconds,
                    "max_output_tokens": policy.max_output_tokens,
                },
                status=mission.status.value,
                correlation_id=mission.correlation_id,
                created_at=mission.created_at,
                updated_at=mission.updated_at,
            )
        )

    async def get(self, mission_id: UUID, *, for_update: bool = False) -> Mission:
        statement = select(t.missions).where(t.missions.c.id == mission_id)
        if for_update:
            statement = statement.with_for_update()
        row = await self._one(statement, "mission", mission_id)
        return Mission(
            id=row.id,
            version=row.version,
            objective=row.objective,
            constraints=tuple(row.constraints),
            success_criteria=tuple(row.success_criteria),
            required_capabilities=tuple(row.required_capabilities),
            budget=Budget(row.budget_micro_usd),
            deadline=row.deadline,
            risk_policy=RiskPolicy(RiskLevel(row.max_risk)),
            execution_policy=ExecutionPolicy(**row.execution_policy),
            status=MissionStatus(row.status),
            correlation_id=row.correlation_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def update(self, mission: Mission) -> Mission:
        """Only lifecycle fields change after creation; everything else is fixed at submit."""
        result = await self._connection.execute(
            update(t.missions)
            .where(t.missions.c.id == mission.id, t.missions.c.version == mission.version)
            .values(
                status=mission.status.value,
                updated_at=mission.updated_at,
                version=mission.version + 1,
            )
        )
        if result.rowcount != 1:
            raise ConcurrentUpdateError("mission", mission.id)
        return _with_version(mission, mission.version + 1)


# --- Tasks -----------------------------------------------------------------------------


class _Tasks(_Repository):
    async def add(self, task: Task) -> None:
        await self._connection.execute(
            insert(t.tasks).values(
                id=task.id,
                version=task.version,
                mission_id=task.mission_id,
                objective=task.objective,
                agent_id=task.agent_id,
                allowed_tools=list(task.allowed_tools),
                status=task.status.value,
                created_at=task.created_at,
                updated_at=task.updated_at,
            )
        )

    async def get(self, task_id: UUID, *, for_update: bool = False) -> Task:
        statement = select(t.tasks).where(t.tasks.c.id == task_id)
        if for_update:
            statement = statement.with_for_update()
        return _task(await self._one(statement, "task", task_id))

    async def update(self, task: Task) -> Task:
        result = await self._connection.execute(
            update(t.tasks)
            .where(t.tasks.c.id == task.id, t.tasks.c.version == task.version)
            .values(status=task.status.value, updated_at=task.updated_at, version=task.version + 1)
        )
        if result.rowcount != 1:
            raise ConcurrentUpdateError("task", task.id)
        return _with_version(task, task.version + 1)

    async def list_for_mission(self, mission_id: UUID) -> list[Task]:
        rows = await self._connection.execute(
            select(t.tasks).where(t.tasks.c.mission_id == mission_id).order_by(t.tasks.c.created_at)
        )
        return [_task(row) for row in rows]

    async def lock_next_ready(self) -> Task | None:
        row = (
            await self._connection.execute(
                select(t.tasks)
                .where(t.tasks.c.status == TaskStatus.READY.value)
                .order_by(t.tasks.c.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
        ).first()
        return None if row is None else _task(row)


def _task(row: Row[Any]) -> Task:
    return Task(
        id=row.id,
        version=row.version,
        mission_id=row.mission_id,
        objective=row.objective,
        agent_id=row.agent_id,
        allowed_tools=tuple(row.allowed_tools),
        status=TaskStatus(row.status),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _with_version[T: (Mission, Task)](entity: T, version: int) -> T:
    return replace(entity, version=version)


# --- Executions ------------------------------------------------------------------------


class _Executions(_Repository):
    async def add(self, execution: Execution, *, worker_id: str, lease_seconds: float) -> int:
        fence = 1
        await self._connection.execute(
            insert(t.executions).values(
                **_execution_values(execution),
                id=execution.id,
                mission_id=execution.mission_id,
                task_id=execution.task_id,
                agent_id=execution.agent_id,
                agent_version_id=execution.agent_version_id,
                model_provider=execution.model.provider,
                model_name=execution.model.name,
                attempt=execution.attempt,
                fence=fence,
                lease_owner=worker_id,
                lease_expires_at=_lease_until(lease_seconds),
                started_at=execution.started_at,
            )
        )
        return fence

    async def reclaim_expired(
        self, *, worker_id: str, lease_seconds: float
    ) -> tuple[Execution, int] | None:
        expired = (
            select(t.executions.c.id)
            .where(
                t.executions.c.status == ExecutionStatus.RUNNING.value,
                t.executions.c.lease_expires_at < func.now(),
            )
            .order_by(t.executions.c.lease_expires_at)
            .limit(1)
            .with_for_update(skip_locked=True)
            .scalar_subquery()
        )
        row = (
            await self._connection.execute(
                update(t.executions)
                .where(t.executions.c.id == expired)
                .values(
                    attempt=t.executions.c.attempt + 1,
                    fence=t.executions.c.fence + 1,
                    lease_owner=worker_id,
                    lease_expires_at=_lease_until(lease_seconds),
                )
                .returning(t.executions)
            )
        ).first()
        return None if row is None else (_execution(row), row.fence)

    async def get(self, execution_id: UUID) -> Execution:
        statement = select(t.executions).where(t.executions.c.id == execution_id)
        return _execution(await self._one(statement, "execution", execution_id))

    async def list_for_mission(self, mission_id: UUID) -> list[Execution]:
        rows = await self._connection.execute(
            select(t.executions)
            .where(t.executions.c.mission_id == mission_id)
            .order_by(t.executions.c.started_at)
        )
        return [_execution(row) for row in rows]

    async def update(self, execution: Execution, *, fence: int, lease_seconds: float) -> None:
        running = execution.status is ExecutionStatus.RUNNING
        result = await self._connection.execute(
            update(t.executions)
            .where(*self._held(execution.id, fence))
            .values(
                **_execution_values(execution),
                lease_owner=t.executions.c.lease_owner if running else None,
                lease_expires_at=_lease_until(lease_seconds) if running else None,
            )
        )
        if result.rowcount != 1:
            raise LeaseLostError(execution.id, fence)

    async def renew(self, execution_id: UUID, *, fence: int, lease_seconds: float) -> bool:
        result = await self._connection.execute(
            update(t.executions)
            .where(*self._held(execution_id, fence))
            .values(lease_expires_at=_lease_until(lease_seconds))
        )
        return result.rowcount == 1

    async def release(self, execution_id: UUID, *, fence: int) -> None:
        await self._connection.execute(
            update(t.executions)
            .where(*self._held(execution_id, fence))
            .values(lease_expires_at=func.now())
        )

    async def mission_spend(self, mission_id: UUID) -> int:
        total = await self._connection.scalar(
            select(func.coalesce(func.sum(t.executions.c.cost_micro_usd), 0)).where(
                t.executions.c.mission_id == mission_id
            )
        )
        return int(total or 0)

    @staticmethod
    def _held(execution_id: UUID, fence: int) -> tuple[Any, ...]:
        return (
            t.executions.c.id == execution_id,
            t.executions.c.fence == fence,
            t.executions.c.status == ExecutionStatus.RUNNING.value,
        )


def _execution_values(execution: Execution) -> dict[str, Any]:
    return {
        "status": execution.status.value,
        "input_tokens": execution.usage.input_tokens,
        "output_tokens": execution.usage.output_tokens,
        "cache_read_input_tokens": execution.usage.cache_read_input_tokens,
        "cache_creation_input_tokens": execution.usage.cache_creation_input_tokens,
        "cost_micro_usd": execution.cost_micro_usd,
        "output": execution.output,
        "failure_kind": None if execution.failure_kind is None else execution.failure_kind.value,
        "failure_detail": execution.failure_detail,
        "completed_at": execution.completed_at,
    }


def _execution(row: Row[Any]) -> Execution:
    return Execution(
        id=row.id,
        mission_id=row.mission_id,
        task_id=row.task_id,
        agent_id=row.agent_id,
        agent_version_id=row.agent_version_id,
        model=ModelRef(row.model_provider, row.model_name),
        status=ExecutionStatus(row.status),
        attempt=row.attempt,
        usage=Usage(
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
            cache_read_input_tokens=row.cache_read_input_tokens,
            cache_creation_input_tokens=row.cache_creation_input_tokens,
        ),
        cost_micro_usd=row.cost_micro_usd,
        output=row.output,
        failure_kind=None if row.failure_kind is None else FailureKind(row.failure_kind),
        failure_detail=row.failure_detail,
        started_at=row.started_at,
        completed_at=row.completed_at,
    )


# --- Checkpoints -----------------------------------------------------------------------


class _Checkpoints(_Repository):
    async def add(self, checkpoint: Checkpoint) -> None:
        await self._connection.execute(
            insert(t.checkpoints).values(
                execution_id=checkpoint.execution_id,
                sequence=checkpoint.sequence,
                state=checkpoint.state.to_json(),
                state_version=checkpoint.state_version,
                created_at=checkpoint.created_at,
            )
        )

    async def latest(self, execution_id: UUID) -> Checkpoint | None:
        row = (
            await self._connection.execute(
                select(t.checkpoints)
                .where(t.checkpoints.c.execution_id == execution_id)
                .order_by(t.checkpoints.c.sequence.desc())
                .limit(1)
            )
        ).first()
        return None if row is None else _checkpoint(row)

    async def list(self, execution_id: UUID) -> list[Checkpoint]:
        rows = await self._connection.execute(
            select(t.checkpoints)
            .where(t.checkpoints.c.execution_id == execution_id)
            .order_by(t.checkpoints.c.sequence)
        )
        return [_checkpoint(row) for row in rows]


def _checkpoint(row: Row[Any]) -> Checkpoint:
    return Checkpoint(
        execution_id=row.execution_id,
        sequence=row.sequence,
        state=AgentState.from_json(row.state),
        state_version=row.state_version,
        created_at=row.created_at,
    )


# --- Tool invocation ledger ------------------------------------------------------------


class _ToolInvocations(_Repository):
    async def get(self, idempotency_key: str) -> ToolInvocation | None:
        row = (
            await self._connection.execute(
                select(t.tool_invocations).where(
                    t.tool_invocations.c.idempotency_key == idempotency_key
                )
            )
        ).first()
        return None if row is None else _invocation(row)

    async def record_intent(self, invocation: ToolInvocation) -> ToolInvocation:
        statement = pg_insert(t.tool_invocations).values(
            idempotency_key=invocation.idempotency_key,
            execution_id=invocation.execution_id,
            turn=invocation.turn,
            tool_use_id=invocation.tool_use_id,
            tool=invocation.tool,
            tool_version=invocation.tool_version,
            status=InvocationStatus.INTENDED.value,
            invocations=1,
            created_at=func.now(),
            updated_at=func.now(),
        )
        upsert = statement.on_conflict_do_update(
            index_elements=[t.tool_invocations.c.idempotency_key],
            set_={
                "invocations": t.tool_invocations.c.invocations + 1,
                "updated_at": func.now(),
            },
        ).returning(t.tool_invocations)
        row = (await self._connection.execute(upsert)).one()
        return _invocation(row)

    async def record_outcome(self, idempotency_key: str, status: InvocationStatus) -> None:
        await self._connection.execute(
            update(t.tool_invocations)
            .where(t.tool_invocations.c.idempotency_key == idempotency_key)
            .values(status=status.value, updated_at=func.now())
        )


def _invocation(row: Row[Any]) -> ToolInvocation:
    return ToolInvocation(
        idempotency_key=row.idempotency_key,
        execution_id=row.execution_id,
        turn=row.turn,
        tool_use_id=row.tool_use_id,
        tool=row.tool,
        tool_version=row.tool_version,
        status=InvocationStatus(row.status),
        invocations=row.invocations,
    )


# --- Events ----------------------------------------------------------------------------

_APPEND_EVENT = text(
    """
    INSERT INTO events (id, event_type, aggregate_type, aggregate_id, sequence, mission_id,
                        payload, correlation_id, causation_id, occurred_at)
    SELECT :id, :event_type, :aggregate_type, :aggregate_id, COALESCE(MAX(sequence), 0) + 1,
           :mission_id, CAST(:payload AS jsonb), :correlation_id, :causation_id, clock_timestamp()
    FROM events WHERE aggregate_id = :aggregate_id
    RETURNING sequence, occurred_at
    """
)


class _Events(_Repository):
    async def append(
        self,
        event: DomainEvent,
        *,
        correlation_id: str | None,
        causation_id: UUID | None = None,
    ) -> EventRecord:
        event_id = uuid4()
        payload = event.payload()
        row = (
            await self._connection.execute(
                _APPEND_EVENT,
                {
                    "id": event_id,
                    "event_type": event.event_type,
                    "aggregate_type": event.aggregate_type,
                    "aggregate_id": event.aggregate_id,
                    "mission_id": event.mission_id,
                    "payload": json.dumps(payload),
                    "correlation_id": correlation_id,
                    "causation_id": causation_id,
                },
            )
        ).one()
        return EventRecord(
            id=event_id,
            event_type=event.event_type,
            aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id,
            sequence=row.sequence,
            mission_id=event.mission_id,
            payload=payload,
            correlation_id=correlation_id,
            causation_id=causation_id,
            occurred_at=row.occurred_at,
        )

    async def list_for_mission(self, mission_id: UUID) -> list[EventRecord]:
        return await self._list(t.events.c.mission_id == mission_id)

    async def list_for_aggregate(self, aggregate_id: UUID) -> list[EventRecord]:
        return await self._list(t.events.c.aggregate_id == aggregate_id)

    async def _list(self, condition: Any) -> list[EventRecord]:
        rows = await self._connection.execute(
            select(t.events).where(condition).order_by(t.events.c.occurred_at, t.events.c.sequence)
        )
        return [_event(row) for row in rows]


def _event(row: Row[Any]) -> EventRecord:
    payload: JSONObject = row.payload
    return EventRecord(
        id=row.id,
        event_type=row.event_type,
        aggregate_type=row.aggregate_type,
        aggregate_id=row.aggregate_id,
        sequence=row.sequence,
        mission_id=row.mission_id,
        payload=payload,
        correlation_id=row.correlation_id,
        causation_id=row.causation_id,
        occurred_at=row.occurred_at,
    )
