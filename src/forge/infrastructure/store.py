"""PostgreSQL implementation of the persistence ports (forge.domain.repositories).

Every lease comparison uses database time so workers never compare clocks.
"""

import json
from dataclasses import replace
from datetime import datetime
from types import TracebackType
from typing import Any, Self
from uuid import UUID, uuid4

from sqlalchemy import Row, and_, delete, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncTransaction

from forge.domain.agent import Agent, AgentVersion, ModelRef
from forge.domain.capability import Capability, CapabilityGraph, Competency, Reliability
from forge.domain.checkpoint import AgentState, Checkpoint
from forge.domain.common import JSONObject
from forge.domain.cost import Usage
from forge.domain.evaluation import (
    Benchmark,
    BenchmarkCase,
    BenchmarkRun,
    BenchmarkRunItem,
    BenchmarkRunStatus,
    Dimension,
    DimensionResult,
    Evaluation,
    Expectations,
    Finding,
    Scope,
    ScopedResult,
)
from forge.domain.events import DomainEvent, EventRecord
from forge.domain.execution import Execution, ExecutionStatus, FailureKind
from forge.domain.governance import (
    ActorType,
    Approval,
    ApprovalStatus,
    AuditRecord,
    Condition,
    Effect,
    Operator,
    Policy,
    Rule,
)
from forge.domain.mission import Budget, ExecutionPolicy, Mission, MissionStatus, RiskPolicy
from forge.domain.repositories import (
    AlreadyExistsError,
    ConcurrentUpdateError,
    LeaseLostError,
    NotFoundError,
)
from forge.domain.risk import RiskLevel
from forge.domain.skill import CycleStatus, LearningCycle, SkillRef, SkillStatus, SkillVersion
from forge.domain.task import Task, TaskKind, TaskStatus
from forge.domain.tool import (
    InvocationStatus,
    RegisteredTool,
    ToolInvocation,
    ToolSource,
    ToolStatus,
    ToolVersionRecord,
)
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
        self.tool_registry = _ToolRegistry(connection)
        self.budget = _Budget(connection)
        self.evaluations = _Evaluations(connection)
        self.benchmarks = _Benchmarks(connection)
        self.capabilities = _Capabilities(connection)
        self.competencies = _Competencies(connection)
        self.skills = _Skills(connection)
        self.learning = _Learning(connection)
        self.policies = _Policies(connection)
        self.approvals = _Approvals(connection)
        self.audit = _Audit(connection)
        self.critical = _Critical(connection)
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
    async def add_version(self, version: AgentVersion) -> None:
        await self._connection.execute(
            insert(t.agent_versions).values(**_agent_version_values(version))
        )

    async def list_versions(self, agent_id: UUID) -> list[AgentVersion]:
        rows = await self._connection.execute(
            select(t.agent_versions)
            .where(t.agent_versions.c.agent_id == agent_id)
            .order_by(t.agent_versions.c.version)
        )
        return [_agent_version(row) for row in rows]

    async def set_current(self, agent_id: UUID, version: int) -> None:
        result = await self._connection.execute(
            update(t.agents).where(t.agents.c.id == agent_id).values(current_version=version)
        )
        if result.rowcount != 1:
            raise NotFoundError("agent", agent_id)

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
            insert(t.agent_versions).values(**_agent_version_values(version))
        )

    async def get(self, agent_id: UUID) -> Agent:
        row = await self._one(select(t.agents).where(t.agents.c.id == agent_id), "agent", agent_id)
        return Agent(
            id=row.id, name=row.name, current_version=row.current_version, created_at=row.created_at
        )

    async def list_current(self) -> list[tuple[Agent, AgentVersion]]:
        rows = await self._connection.execute(
            select(
                t.agent_versions,
                t.agents.c.name.label("agent_name"),
                t.agents.c.current_version.label("agent_current_version"),
                t.agents.c.created_at.label("agent_created_at"),
            )
            .join(
                t.agents,
                and_(
                    t.agents.c.id == t.agent_versions.c.agent_id,
                    t.agents.c.current_version == t.agent_versions.c.version,
                ),
            )
            .order_by(t.agents.c.name)
        )
        return [
            (
                Agent(
                    row.agent_id, row.agent_name, row.agent_current_version, row.agent_created_at
                ),
                _agent_version(row),
            )
            for row in rows
        ]

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


def _agent_version_values(version: AgentVersion) -> dict[str, Any]:
    return {
        "id": version.id,
        "agent_id": version.agent_id,
        "version": version.version,
        "role": version.role,
        "instructions": version.instructions,
        "model_provider": version.model.provider,
        "model_name": version.model.name,
        "tools": list(version.tools),
        "created_at": version.created_at,
        "fallback_models": [[m.provider, m.name] for m in version.fallback_models],
        "model_options": dict(version.model_options),
        "capabilities": list(version.capabilities),
        "skills": [[s.name, s.version] for s in version.skills],
    }


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
        capabilities=tuple(row.capabilities),
        skills=tuple(SkillRef(name, version) for name, version in row.skills),
        fallback_models=tuple(ModelRef(provider, name) for provider, name in row.fallback_models),
        model_options=row.model_options,
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
                tool_scope=None if mission.tool_scope is None else list(mission.tool_scope),
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
            tool_scope=None if row.tool_scope is None else tuple(row.tool_scope),
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
                key=task.key,
                kind=task.kind.value,
                dependencies=[str(d) for d in task.dependencies],
                required_capabilities=list(task.required_capabilities),
                max_attempts=task.max_attempts,
                attempts=task.attempts,
                agent_version_id=task.agent_version_id,
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
            .values(
                status=task.status.value,
                updated_at=task.updated_at,
                version=task.version + 1,
                agent_id=task.agent_id,
                allowed_tools=list(task.allowed_tools),
                attempts=task.attempts,
            )
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
        # Lock the mission with the task, mission first in intent: cancellation locks the
        # mission then its tasks, so a claim must never wait on a mission lock (deadlock).
        row = (
            await self._connection.execute(
                select(t.tasks)
                .join(t.missions, t.missions.c.id == t.tasks.c.mission_id)
                .where(t.tasks.c.status == TaskStatus.READY.value, t.tasks.c.agent_id.is_not(None))
                .order_by(t.tasks.c.created_at)
                .limit(1)
                .with_for_update(skip_locked=True, of=[t.tasks, t.missions])
            )
        ).first()
        return None if row is None else _task(row)

    async def workload(self) -> dict[UUID, int]:
        rows = await self._connection.execute(
            select(t.tasks.c.agent_id, func.count())
            .where(
                t.tasks.c.status.in_([TaskStatus.READY.value, TaskStatus.RUNNING.value]),
                t.tasks.c.agent_id.is_not(None),
            )
            .group_by(t.tasks.c.agent_id)
        )
        return {row[0]: int(row[1]) for row in rows}


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
        key=row.key,
        kind=TaskKind(row.kind),
        dependencies=tuple(UUID(d) for d in row.dependencies),
        required_capabilities=tuple(row.required_capabilities),
        max_attempts=row.max_attempts,
        attempts=row.attempts,
        agent_version_id=row.agent_version_id,
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
                task_attempt=execution.task_attempt,
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

    async def latest_for_task(self, task_id: UUID) -> Execution | None:
        row = (
            await self._connection.execute(
                select(t.executions)
                .where(t.executions.c.task_id == task_id)
                .order_by(t.executions.c.task_attempt.desc())
                .limit(1)
            )
        ).first()
        return None if row is None else _execution(row)

    async def reclaim_decided(
        self, *, worker_id: str, lease_seconds: float
    ) -> tuple[Execution, int] | None:
        decided = (
            select(t.executions.c.id)
            .where(
                t.executions.c.status == ExecutionStatus.WAITING_APPROVAL.value,
                select(t.approvals.c.id)
                .where(
                    t.approvals.c.execution_id == t.executions.c.id,
                    t.approvals.c.status != "PENDING",  # expiry is closed by expire_due first
                )
                .exists()
                | select(t.missions.c.id)
                .where(
                    t.missions.c.id == t.executions.c.mission_id,
                    t.missions.c.status.in_(["COMPLETED", "FAILED", "CANCELLED"])
                    | (t.missions.c.deadline <= func.now()),
                )
                .exists()
                | select(t.tasks.c.id)
                .where(t.tasks.c.id == t.executions.c.task_id, t.tasks.c.status == "CANCELLED")
                .exists(),
            )
            .order_by(t.executions.c.started_at)
            .limit(1)
            .with_for_update(skip_locked=True)
            .scalar_subquery()
        )
        row = (
            await self._connection.execute(
                update(t.executions)
                .where(t.executions.c.id == decided)
                .values(
                    status=ExecutionStatus.RUNNING.value,
                    # a resume after a decision is not a crash recovery: attempt unchanged
                    fence=t.executions.c.fence + 1,
                    lease_owner=worker_id,
                    lease_expires_at=_lease_until(lease_seconds),
                )
                .returning(t.executions)
            )
        ).first()
        return None if row is None else (_execution(row), row.fence)

    async def reliability(self, agent_ids: list[UUID]) -> dict[UUID, Reliability]:
        if not agent_ids:
            return {}
        rows = await self._connection.execute(_RELIABILITY, {"agent_ids": agent_ids})
        return {
            row.agent_id: Reliability(
                successes=int(row.successes),
                failures=int(row.failures),
                mean_cost_micro_usd=None if row.mean_cost is None else float(row.mean_cost),
                mean_latency_ms=None if row.mean_latency is None else float(row.mean_latency),
            )
            for row in rows
        }

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
        task_attempt=row.task_attempt,
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


# --- Tool registry ---------------------------------------------------------------------


class _ToolRegistry(_Repository):
    async def get(self, name: str, *, for_update: bool = False) -> RegisteredTool | None:
        statement = select(t.tools).where(t.tools.c.name == name)
        if for_update:
            statement = statement.with_for_update()
        row = (await self._connection.execute(statement)).first()
        return None if row is None else _registered(row)

    async def list(self) -> list[RegisteredTool]:
        rows = await self._connection.execute(select(t.tools).order_by(t.tools.c.name))
        return [_registered(row) for row in rows]

    async def get_version(self, name: str, version: str) -> ToolVersionRecord | None:
        row = (
            await self._connection.execute(
                select(t.tool_versions).where(
                    t.tool_versions.c.name == name, t.tool_versions.c.version == version
                )
            )
        ).first()
        if row is None:
            return None
        return ToolVersionRecord(
            name=row.name,
            version=row.version,
            source=ToolSource(row.source),
            source_ref=row.source_ref,
            definition_sha256=row.definition_sha256,
            registered_at=row.registered_at,
        )

    async def add_version(self, record: ToolVersionRecord) -> None:
        await self._connection.execute(
            pg_insert(t.tool_versions)
            .values(
                name=record.name,
                version=record.version,
                source=record.source.value,
                source_ref=record.source_ref,
                definition_sha256=record.definition_sha256,
                registered_at=record.registered_at,
            )
            .on_conflict_do_nothing()
        )

    async def save(self, tool: RegisteredTool) -> None:
        values = {
            "pinned_version": tool.pinned_version,
            "pinned_sha256": tool.pinned_sha256,
            "status": tool.status.value,
            "reason": tool.reason,
            "updated_at": tool.updated_at,
        }
        await self._connection.execute(
            pg_insert(t.tools)
            .values(name=tool.name, **values)
            .on_conflict_do_update(index_elements=[t.tools.c.name], set_=values)
        )


def _registered(row: Row[Any]) -> RegisteredTool:
    return RegisteredTool(
        name=row.name,
        pinned_version=row.pinned_version,
        pinned_sha256=row.pinned_sha256,
        status=ToolStatus(row.status),
        reason=row.reason,
        updated_at=row.updated_at,
    )


# --- Budget reservations ---------------------------------------------------------------


class _Budget(_Repository):
    async def reserved(self, mission_id: UUID, *, excluding: UUID | None = None) -> int:
        statement = select(
            func.coalesce(func.sum(t.budget_reservations.c.amount_micro_usd), 0)
        ).where(t.budget_reservations.c.mission_id == mission_id)
        if excluding is not None:
            statement = statement.where(t.budget_reservations.c.execution_id != excluding)
        return int(await self._connection.scalar(statement) or 0)

    async def reserve(
        self, execution_id: UUID, mission_id: UUID, amount: int, now: datetime
    ) -> None:
        values = {"mission_id": mission_id, "amount_micro_usd": amount, "created_at": now}
        await self._connection.execute(
            pg_insert(t.budget_reservations)
            .values(execution_id=execution_id, **values)
            .on_conflict_do_update(
                index_elements=[t.budget_reservations.c.execution_id], set_=values
            )
        )

    async def release(self, execution_id: UUID) -> None:
        await self._connection.execute(
            delete(t.budget_reservations).where(
                t.budget_reservations.c.execution_id == execution_id
            )
        )


# --- Evaluations -------------------------------------------------------------------------


class _Evaluations(_Repository):
    async def add(self, evaluation: Evaluation, *, trigger: str) -> bool:
        try:
            async with self._connection.begin_nested():
                await self._connection.execute(
                    insert(t.evaluations).values(
                        id=evaluation.id,
                        mission_id=evaluation.mission_id,
                        evaluator_set=evaluation.evaluator_set,
                        benchmark_case=evaluation.benchmark_case,
                        trigger=trigger,
                        cost_micro_usd=evaluation.cost_micro_usd,
                        created_at=evaluation.created_at,
                    )
                )
        except IntegrityError:
            return False  # another worker already evaluated this mission automatically
        if evaluation.results:
            await self._connection.execute(
                insert(t.evaluation_results),
                [
                    {
                        "evaluation_id": evaluation.id,
                        "scope": scoped.scope.value,
                        "task_id": scoped.task_id,
                        "agent_id": scoped.agent_id,
                        "execution_id": scoped.execution_id,
                        "dimension": scoped.result.dimension.value,
                        "score": scoped.result.score,
                        "confidence": scoped.result.confidence,
                        "evaluator": scoped.result.evaluator,
                        "findings": [
                            {"code": f.code, "message": f.message, "refs": list(f.refs)}
                            for f in scoped.result.findings
                        ],
                        "metrics": dict(scoped.result.metrics),
                    }
                    for scoped in evaluation.results
                ],
            )
        return True

    async def list_for_mission(self, mission_id: UUID) -> list[Evaluation]:
        rows = await self._connection.execute(
            select(t.evaluations)
            .where(t.evaluations.c.mission_id == mission_id)
            .order_by(t.evaluations.c.created_at)
        )
        return [await self._hydrate(row) for row in rows.fetchall()]

    async def missions_awaiting_evaluation(self, limit: int) -> list[UUID]:
        evaluated = (
            select(t.evaluations.c.mission_id)
            .where(t.evaluations.c.trigger == "auto")
            .scalar_subquery()
        )
        rows = await self._connection.execute(
            select(t.missions.c.id)
            .where(
                t.missions.c.status.in_(["COMPLETED", "FAILED", "CANCELLED"]),
                t.missions.c.id.not_in(evaluated),
            )
            .order_by(t.missions.c.updated_at)
            .limit(limit)
        )
        return [row[0] for row in rows]

    async def results_for_agent(self, agent_id: UUID) -> list[tuple[Evaluation, ScopedResult]]:
        rows = await self._connection.execute(
            select(t.evaluations.c.id)
            .join(t.evaluation_results, t.evaluation_results.c.evaluation_id == t.evaluations.c.id)
            .where(
                t.evaluation_results.c.agent_id == agent_id,
                t.evaluation_results.c.scope == Scope.TASK.value,
            )
            .group_by(t.evaluations.c.id, t.evaluations.c.created_at)
            .order_by(t.evaluations.c.created_at)
        )
        pairs: list[tuple[Evaluation, ScopedResult]] = []
        for (evaluation_id,) in rows.fetchall():
            row = (
                await self._connection.execute(
                    select(t.evaluations).where(t.evaluations.c.id == evaluation_id)
                )
            ).one()
            evaluation = await self._hydrate(row)
            pairs.extend(
                (evaluation, scoped)
                for scoped in evaluation.results
                if scoped.agent_id == agent_id and scoped.scope is Scope.TASK
            )
        return pairs

    async def _hydrate(self, row: Row[Any]) -> Evaluation:
        results = await self._connection.execute(
            select(t.evaluation_results)
            .where(t.evaluation_results.c.evaluation_id == row.id)
            .order_by(t.evaluation_results.c.id)
        )
        return Evaluation(
            id=row.id,
            mission_id=row.mission_id,
            evaluator_set=row.evaluator_set,
            benchmark_case=row.benchmark_case,
            results=tuple(
                ScopedResult(
                    scope=Scope(r.scope),
                    task_id=r.task_id,
                    agent_id=r.agent_id,
                    execution_id=r.execution_id,
                    result=DimensionResult(
                        dimension=Dimension(r.dimension),
                        score=r.score,
                        confidence=r.confidence,
                        evaluator=r.evaluator,
                        findings=tuple(
                            Finding(f["code"], f["message"], tuple(f["refs"])) for f in r.findings
                        ),
                        metrics=r.metrics,
                    ),
                )
                for r in results
            ),
            cost_micro_usd=row.cost_micro_usd,
            created_at=row.created_at,
        )


# --- Benchmarks --------------------------------------------------------------------------


class _Benchmarks(_Repository):
    async def add(self, benchmark: Benchmark, now: datetime) -> None:
        await self._connection.execute(
            pg_insert(t.benchmarks)
            .values(
                name=benchmark.name,
                version=benchmark.version,
                capability=benchmark.capability,
                description=benchmark.description,
                cases=[_case_json(case) for case in benchmark.cases],
                created_at=now,
            )
            .on_conflict_do_nothing()
        )

    async def get(self, name: str, version: int | None = None) -> Benchmark:
        statement = select(t.benchmarks).where(t.benchmarks.c.name == name)
        if version is None:
            statement = statement.order_by(t.benchmarks.c.version.desc()).limit(1)
        else:
            statement = statement.where(t.benchmarks.c.version == version)
        return _benchmark(await self._one(statement, "benchmark", f"{name}@{version or 'latest'}"))

    async def list_all(self) -> list[Benchmark]:
        rows = await self._connection.execute(
            select(t.benchmarks).order_by(t.benchmarks.c.name, t.benchmarks.c.version)
        )
        return [_benchmark(row) for row in rows]

    async def add_run(self, run: BenchmarkRun, items: list[BenchmarkRunItem]) -> None:
        await self._connection.execute(
            insert(t.benchmark_runs).values(
                id=run.id,
                benchmark=run.benchmark,
                version=run.version,
                agent_id=run.agent_id,
                status=run.status.value,
                summary=dict(run.summary),
                created_at=run.created_at,
                completed_at=run.completed_at,
            )
        )
        await self._connection.execute(
            insert(t.benchmark_run_items),
            [
                {"run_id": i.run_id, "case_key": i.case_key, "mission_id": i.mission_id}
                for i in items
            ],
        )

    async def get_run(self, run_id: UUID, *, for_update: bool = False) -> BenchmarkRun:
        statement = select(t.benchmark_runs).where(t.benchmark_runs.c.id == run_id)
        if for_update:
            statement = statement.with_for_update()
        row = await self._one(statement, "benchmark run", run_id)
        return BenchmarkRun(
            id=row.id,
            benchmark=row.benchmark,
            version=row.version,
            agent_id=row.agent_id,
            status=BenchmarkRunStatus(row.status),
            summary=row.summary,
            created_at=row.created_at,
            completed_at=row.completed_at,
        )

    async def run_items(self, run_id: UUID) -> list[BenchmarkRunItem]:
        rows = await self._connection.execute(
            select(t.benchmark_run_items)
            .where(t.benchmark_run_items.c.run_id == run_id)
            .order_by(t.benchmark_run_items.c.case_key)
        )
        return [BenchmarkRunItem(r.run_id, r.case_key, r.mission_id) for r in rows]

    async def item_for_mission(self, mission_id: UUID) -> BenchmarkRunItem | None:
        row = (
            await self._connection.execute(
                select(t.benchmark_run_items).where(
                    t.benchmark_run_items.c.mission_id == mission_id
                )
            )
        ).first()
        return None if row is None else BenchmarkRunItem(row.run_id, row.case_key, row.mission_id)

    async def complete_run(self, run: BenchmarkRun) -> None:
        await self._connection.execute(
            update(t.benchmark_runs)
            .where(t.benchmark_runs.c.id == run.id)
            .values(
                status=run.status.value, summary=dict(run.summary), completed_at=run.completed_at
            )
        )


def _case_json(case: BenchmarkCase) -> dict[str, Any]:
    e = case.expectations
    return {
        "key": case.key,
        "objective": case.objective,
        "required_capabilities": list(case.required_capabilities),
        "difficulty": case.difficulty,
        "risk": case.risk,
        "expectations": {
            "must_include": list(e.must_include),
            "must_not_include": list(e.must_not_include),
            "expected_tools": list(e.expected_tools),
            "reference_evidence": list(e.reference_evidence),
            "ideal_turns": e.ideal_turns,
            "reference_cost_micro_usd": e.reference_cost_micro_usd,
        },
    }


def _benchmark(row: Row[Any]) -> Benchmark:
    return Benchmark(
        name=row.name,
        version=row.version,
        capability=row.capability,
        description=row.description,
        cases=tuple(
            BenchmarkCase(
                key=c["key"],
                objective=c["objective"],
                required_capabilities=tuple(c["required_capabilities"]),
                difficulty=c["difficulty"],
                risk=c["risk"],
                expectations=Expectations(
                    must_include=tuple(c["expectations"]["must_include"]),
                    must_not_include=tuple(c["expectations"]["must_not_include"]),
                    expected_tools=tuple(c["expectations"]["expected_tools"]),
                    reference_evidence=tuple(c["expectations"]["reference_evidence"]),
                    ideal_turns=c["expectations"]["ideal_turns"],
                    reference_cost_micro_usd=c["expectations"]["reference_cost_micro_usd"],
                ),
            )
            for c in row.cases
        ),
    )


# Failures attributable to the agent; budget, deadline, cancellation, and routing are not.
_RELIABILITY = text(
    """
    SELECT e.agent_id,
           count(*) FILTER (WHERE e.status = 'COMPLETED') AS successes,
           count(*) FILTER (WHERE e.status = 'FAILED' AND e.failure_kind IN
               ('MODEL_FAILED', 'MAX_STEPS_EXCEEDED', 'TOOL_OUTCOME_UNKNOWN',
                'RECOVERY_EXHAUSTED')) AS failures,
           avg(e.cost_micro_usd) FILTER (WHERE e.status = 'COMPLETED') AS mean_cost,
           (SELECT avg((ev.payload->>'latency_ms')::float)
              FROM events ev JOIN executions x ON x.id = ev.aggregate_id
             WHERE ev.event_type = 'model.called' AND x.agent_id = e.agent_id) AS mean_latency
      FROM executions e
     WHERE e.agent_id = ANY(:agent_ids)
     GROUP BY e.agent_id
    """
)


# --- Capabilities ------------------------------------------------------------------------


class _Capabilities(_Repository):
    async def save(self, capability: Capability, now: datetime) -> None:
        values = {
            "description": capability.description,
            "parent": capability.parent,
            "prerequisites": list(capability.prerequisites),
            "related": list(capability.related),
            "required_tools": list(capability.required_tools),
        }
        await self._connection.execute(
            pg_insert(t.capabilities)
            .values(name=capability.name, created_at=now, **values)
            .on_conflict_do_update(index_elements=[t.capabilities.c.name], set_=values)
        )

    async def graph(self) -> CapabilityGraph:
        rows = await self._connection.execute(
            select(t.capabilities).order_by(t.capabilities.c.name)
        )
        return CapabilityGraph(
            {
                row.name: Capability(
                    name=row.name,
                    description=row.description,
                    parent=row.parent,
                    prerequisites=tuple(row.prerequisites),
                    related=tuple(row.related),
                    required_tools=tuple(row.required_tools),
                )
                for row in rows
            }
        )


class _Competencies(_Repository):
    async def save(self, competency: Competency, now: datetime) -> None:
        values = {
            "alpha": competency.alpha,
            "beta": competency.beta,
            "sample_count": competency.sample_count,
            "effective_samples": competency.effective_samples,
            "recent_trend": competency.recent_trend,
            "calibration": competency.calibration,
            "last_evaluated": competency.last_evaluated,
            "supporting_evaluations": [str(e) for e in competency.supporting_evaluations],
            "updated_at": now,
        }
        await self._connection.execute(
            pg_insert(t.competencies)
            .values(agent_id=competency.agent_id, capability=competency.capability, **values)
            .on_conflict_do_update(
                index_elements=[t.competencies.c.agent_id, t.competencies.c.capability], set_=values
            )
        )

    async def for_agents(self, agent_ids: list[UUID]) -> list[Competency]:
        if not agent_ids:
            return []
        rows = await self._connection.execute(
            select(t.competencies)
            .where(t.competencies.c.agent_id.in_(agent_ids))
            .order_by(t.competencies.c.agent_id, t.competencies.c.capability)
        )
        return [
            Competency(
                agent_id=row.agent_id,
                capability=row.capability,
                alpha=row.alpha,
                beta=row.beta,
                sample_count=row.sample_count,
                effective_samples=row.effective_samples,
                recent_trend=row.recent_trend,
                calibration=row.calibration,
                last_evaluated=row.last_evaluated,
                supporting_evaluations=tuple(UUID(e) for e in row.supporting_evaluations),
            )
            for row in rows
        ]


# --- Skills and learning -----------------------------------------------------------------


class _Skills(_Repository):
    async def add(self, skill: SkillVersion) -> None:
        await self._connection.execute(
            insert(t.skill_versions).values(
                name=skill.name,
                version=skill.version,
                capability=skill.capability,
                instructions=skill.instructions,
                status=skill.status.value,
                provenance=dict(skill.provenance),
                parent_version=skill.parent_version,
                validation=dict(skill.validation),
                created_at=skill.created_at,
                row_version=skill.row_version,
            )
        )

    async def get(self, name: str, version: int, *, for_update: bool = False) -> SkillVersion:
        statement = select(t.skill_versions).where(
            t.skill_versions.c.name == name, t.skill_versions.c.version == version
        )
        if for_update:
            statement = statement.with_for_update()
        return _skill(await self._one(statement, "skill", f"{name}@{version}"))

    async def next_version(self, name: str) -> int:
        current = await self._connection.scalar(
            select(func.max(t.skill_versions.c.version)).where(t.skill_versions.c.name == name)
        )
        return int(current or 0) + 1

    async def list_all(self) -> list[SkillVersion]:
        rows = await self._connection.execute(
            select(t.skill_versions).order_by(t.skill_versions.c.name, t.skill_versions.c.version)
        )
        return [_skill(row) for row in rows]

    async def update(self, skill: SkillVersion) -> SkillVersion:
        result = await self._connection.execute(
            update(t.skill_versions)
            .where(
                t.skill_versions.c.name == skill.name,
                t.skill_versions.c.version == skill.version,
                t.skill_versions.c.row_version == skill.row_version,
            )
            .values(
                status=skill.status.value,
                validation=dict(skill.validation),
                row_version=skill.row_version + 1,
            )
        )
        if result.rowcount != 1:
            raise ConcurrentUpdateError("skill", f"{skill.name}@{skill.version}")
        return replace(skill, row_version=skill.row_version + 1)


def _skill(row: Row[Any]) -> SkillVersion:
    return SkillVersion(
        name=row.name,
        version=row.version,
        capability=row.capability,
        instructions=row.instructions,
        status=SkillStatus(row.status),
        provenance=row.provenance,
        parent_version=row.parent_version,
        created_at=row.created_at,
        validation=row.validation,
        row_version=row.row_version,
    )


class _Learning(_Repository):
    async def add(self, cycle: LearningCycle) -> None:
        await self._connection.execute(insert(t.learning_cycles).values(**_cycle_values(cycle)))

    async def get(self, cycle_id: UUID, *, for_update: bool = False) -> LearningCycle:
        statement = select(t.learning_cycles).where(t.learning_cycles.c.id == cycle_id)
        if for_update:
            statement = statement.with_for_update()
        return _cycle(await self._one(statement, "learning cycle", cycle_id))

    async def update(self, cycle: LearningCycle) -> LearningCycle:
        values = _cycle_values(cycle)
        values["row_version"] = cycle.row_version + 1
        result = await self._connection.execute(
            update(t.learning_cycles)
            .where(
                t.learning_cycles.c.id == cycle.id,
                t.learning_cycles.c.row_version == cycle.row_version,
            )
            .values(**values)
        )
        if result.rowcount != 1:
            raise ConcurrentUpdateError("learning cycle", cycle.id)
        return replace(cycle, row_version=cycle.row_version + 1)

    async def list_cycles(self, agent_id: UUID | None = None) -> list[LearningCycle]:
        statement = select(t.learning_cycles).order_by(t.learning_cycles.c.created_at)
        if agent_id is not None:
            statement = statement.where(t.learning_cycles.c.agent_id == agent_id)
        return [_cycle(row) for row in await self._connection.execute(statement)]

    async def in_status(self, *statuses: CycleStatus) -> list[LearningCycle]:
        rows = await self._connection.execute(
            select(t.learning_cycles)
            .where(t.learning_cycles.c.status.in_([s.value for s in statuses]))
            .order_by(t.learning_cycles.c.created_at)
        )
        return [_cycle(row) for row in rows]


def _cycle_values(cycle: LearningCycle) -> dict[str, Any]:
    return {
        "id": cycle.id,
        "agent_id": cycle.agent_id,
        "capability": cycle.capability,
        "status": cycle.status.value,
        "analysis": dict(cycle.analysis),
        "gap": dict(cycle.gap),
        "skill_name": None if cycle.skill is None else cycle.skill.name,
        "skill_version": None if cycle.skill is None else cycle.skill.version,
        "dataset": cycle.dataset,
        "baseline_version_id": cycle.baseline_version_id,
        "candidate_version_id": cycle.candidate_version_id,
        "baseline_run_id": cycle.baseline_run_id,
        "candidate_run_id": cycle.candidate_run_id,
        "delta": dict(cycle.delta),
        "created_at": cycle.created_at,
        "updated_at": cycle.updated_at,
        "row_version": cycle.row_version,
    }


def _cycle(row: Row[Any]) -> LearningCycle:
    return LearningCycle(
        id=row.id,
        agent_id=row.agent_id,
        capability=row.capability,
        status=CycleStatus(row.status),
        analysis=row.analysis,
        gap=row.gap,
        skill=None if row.skill_name is None else SkillRef(row.skill_name, row.skill_version),
        dataset=row.dataset,
        baseline_version_id=row.baseline_version_id,
        candidate_version_id=row.candidate_version_id,
        baseline_run_id=row.baseline_run_id,
        candidate_run_id=row.candidate_run_id,
        delta=row.delta,
        created_at=row.created_at,
        updated_at=row.updated_at,
        row_version=row.row_version,
    )


# --- Governance --------------------------------------------------------------------------


class _Policies(_Repository):
    async def add(self, policy: Policy) -> None:
        await self._connection.execute(
            insert(t.policies).values(
                name=policy.name,
                version=policy.version,
                rules=[_rule_json(rule) for rule in policy.rules],
                created_by=policy.created_by,
                created_at=policy.created_at,
            )
        )

    async def next_version(self, name: str) -> int:
        current = await self._connection.scalar(
            select(func.max(t.policies.c.version)).where(t.policies.c.name == name)
        )
        return int(current or 0) + 1

    async def active(self) -> list[Policy]:
        latest = (
            select(t.policies.c.name, func.max(t.policies.c.version).label("version"))
            .group_by(t.policies.c.name)
            .subquery()
        )
        rows = await self._connection.execute(
            select(t.policies)
            .join(
                latest,
                and_(t.policies.c.name == latest.c.name, t.policies.c.version == latest.c.version),
            )
            .order_by(t.policies.c.name)
        )
        return [
            Policy(
                name=row.name,
                version=row.version,
                rules=tuple(_rule(rule) for rule in row.rules),
                created_by=row.created_by,
                created_at=row.created_at,
            )
            for row in rows
        ]


def _rule_json(rule: Rule) -> dict[str, Any]:
    return {
        "effect": rule.effect.value,
        "tools": list(rule.tools),
        "agents": list(rule.agents),
        "reason": rule.reason,
        "conditions": [
            {"argument": c.argument, "operator": c.operator.value, "value": c.value}
            for c in rule.conditions
        ],
    }


def _rule(data: dict[str, Any]) -> Rule:
    return Rule(
        effect=Effect(data["effect"]),
        tools=tuple(data["tools"]),
        agents=tuple(data.get("agents", [])),
        reason=data.get("reason", ""),
        conditions=tuple(
            Condition(c["argument"], Operator(c["operator"]), c["value"])
            for c in data.get("conditions", [])
        ),
    )


class _Approvals(_Repository):
    async def add(self, approval: Approval) -> None:
        await self._connection.execute(insert(t.approvals).values(**_approval_values(approval)))

    async def get(self, approval_id: UUID, *, for_update: bool = False) -> Approval:
        statement = select(t.approvals).where(t.approvals.c.id == approval_id)
        if for_update:
            statement = statement.with_for_update()
        return _approval(await self._one(statement, "approval", approval_id))

    async def latest_for_key(self, idempotency_key: str) -> Approval | None:
        row = (
            await self._connection.execute(
                select(t.approvals)
                .where(t.approvals.c.idempotency_key == idempotency_key)
                .order_by(t.approvals.c.requested_at.desc())
                .limit(1)
            )
        ).first()
        return None if row is None else _approval(row)

    async def update(self, approval: Approval) -> Approval:
        values = _approval_values(approval)
        values["row_version"] = approval.row_version + 1
        result = await self._connection.execute(
            update(t.approvals)
            .where(
                t.approvals.c.id == approval.id, t.approvals.c.row_version == approval.row_version
            )
            .values(**values)
        )
        if result.rowcount != 1:
            raise ConcurrentUpdateError("approval", approval.id)
        return replace(approval, row_version=approval.row_version + 1)

    async def list_approvals(self, status: ApprovalStatus | None = None) -> list[Approval]:
        statement = select(t.approvals).order_by(t.approvals.c.requested_at.desc())
        if status is not None:
            statement = statement.where(t.approvals.c.status == status.value)
        return [_approval(row) for row in await self._connection.execute(statement)]

    async def pending_for_execution(self, execution_id: UUID) -> list[Approval]:
        rows = await self._connection.execute(
            select(t.approvals)
            .where(
                t.approvals.c.execution_id == execution_id,
                t.approvals.c.status == ApprovalStatus.PENDING.value,
            )
            .with_for_update()
        )
        return [_approval(row) for row in rows]

    async def due_for_expiry(self, now: datetime) -> list[Approval]:
        rows = await self._connection.execute(
            select(t.approvals)
            .where(
                t.approvals.c.status == ApprovalStatus.PENDING.value,
                t.approvals.c.expires_at <= now,
            )
            .with_for_update(skip_locked=True)  # an operator deciding it right now wins
        )
        return [_approval(row) for row in rows]


def _approval_values(a: Approval) -> dict[str, Any]:
    return {
        "id": a.id,
        "mission_id": a.mission_id,
        "task_id": a.task_id,
        "execution_id": a.execution_id,
        "idempotency_key": a.idempotency_key,
        "agent_id": a.agent_id,
        "tool": a.tool,
        "arguments": dict(a.arguments),
        "arguments_sha256": a.arguments_sha256,
        "rationale": a.rationale,
        "evidence": list(a.evidence),
        "affected_resources": list(a.affected_resources),
        "risk": a.risk,
        "status": a.status.value,
        "expires_at": a.expires_at,
        "requested_at": a.requested_at,
        "decided_by": a.decided_by,
        "decided_at": a.decided_at,
        "decision_reason": a.decision_reason,
        "row_version": a.row_version,
    }


def _approval(row: Row[Any]) -> Approval:
    return Approval(
        id=row.id,
        mission_id=row.mission_id,
        task_id=row.task_id,
        execution_id=row.execution_id,
        idempotency_key=row.idempotency_key,
        agent_id=row.agent_id,
        tool=row.tool,
        arguments=row.arguments,
        arguments_sha256=row.arguments_sha256,
        rationale=row.rationale,
        evidence=tuple(row.evidence),
        affected_resources=tuple(row.affected_resources),
        risk=row.risk,
        status=ApprovalStatus(row.status),
        expires_at=row.expires_at,
        requested_at=row.requested_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision_reason=row.decision_reason,
        row_version=row.row_version,
    )


class _Audit(_Repository):
    async def add(self, record: AuditRecord) -> None:
        await self._connection.execute(
            insert(t.audit_records).values(
                actor=record.actor,
                actor_type=record.actor_type.value,
                action=record.action,
                decision=record.decision,
                occurred_at=record.occurred_at,
                agent_id=record.agent_id,
                mission_id=record.mission_id,
                tool=record.tool,
                target=record.target,
                approval_id=record.approval_id,
                reason=record.reason,
                arguments_sha256=record.arguments_sha256,
                details=dict(record.details),
            )
        )

    async def list_records(
        self, *, mission_id: UUID | None = None, limit: int = 200
    ) -> list[AuditRecord]:
        statement = select(t.audit_records).order_by(t.audit_records.c.id.desc()).limit(limit)
        if mission_id is not None:
            statement = statement.where(t.audit_records.c.mission_id == mission_id)
        return [
            AuditRecord(
                actor=row.actor,
                actor_type=ActorType(row.actor_type),
                action=row.action,
                decision=row.decision,
                occurred_at=row.occurred_at,
                agent_id=row.agent_id,
                mission_id=row.mission_id,
                tool=row.tool,
                target=row.target,
                approval_id=row.approval_id,
                reason=row.reason,
                arguments_sha256=row.arguments_sha256,
                details=row.details,
            )
            for row in await self._connection.execute(statement)
        ]


class _Critical(_Repository):
    async def enabled(self) -> frozenset[str]:
        rows = await self._connection.execute(
            select(t.critical_enablements.c.tool).where(t.critical_enablements.c.enabled.is_(True))
        )
        return frozenset(row[0] for row in rows)

    async def set(self, tool: str, enabled: bool, by: str, reason: str, now: datetime) -> None:
        values = {"enabled": enabled, "changed_by": by, "reason": reason, "changed_at": now}
        await self._connection.execute(
            pg_insert(t.critical_enablements)
            .values(tool=tool, **values)
            .on_conflict_do_update(index_elements=[t.critical_enablements.c.tool], set_=values)
        )
