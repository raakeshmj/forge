"""Persistence ports. Infrastructure implements them; the domain and services depend only on these.

All repositories of one `UnitOfWork` share a transaction: it commits when the
`async with` block exits normally and rolls back on any exception.
"""

from datetime import datetime
from typing import Protocol, Self
from uuid import UUID

from forge.domain.agent import Agent, AgentVersion
from forge.domain.checkpoint import Checkpoint
from forge.domain.events import DomainEvent, EventRecord
from forge.domain.execution import Execution
from forge.domain.mission import Mission
from forge.domain.task import Task
from forge.domain.tool import InvocationStatus, ToolInvocation


class NotFoundError(LookupError):
    def __init__(self, entity: str, entity_id: object) -> None:
        super().__init__(f"{entity} {entity_id} not found")
        self.entity = entity
        self.entity_id = entity_id


class ConcurrentUpdateError(RuntimeError):
    """An optimistic-version update lost a race; reload and decide again."""

    def __init__(self, entity: str, entity_id: UUID) -> None:
        super().__init__(f"{entity} {entity_id} was modified concurrently")
        self.entity = entity
        self.entity_id = entity_id


class LeaseLostError(RuntimeError):
    """The worker no longer holds the execution's lease; it must stop without writing."""

    def __init__(self, execution_id: UUID, fence: int) -> None:
        super().__init__(f"lease on execution {execution_id} (fence {fence}) is no longer held")
        self.execution_id = execution_id
        self.fence = fence


class AgentRepository(Protocol):
    async def add(self, agent: Agent, version: AgentVersion) -> None: ...
    async def get(self, agent_id: UUID) -> Agent: ...
    async def get_version(self, version_id: UUID) -> AgentVersion: ...
    async def get_current_version(self, agent_id: UUID) -> AgentVersion: ...


class MissionRepository(Protocol):
    async def add(self, mission: Mission) -> None: ...
    async def get(self, mission_id: UUID, *, for_update: bool = False) -> Mission: ...
    async def update(self, mission: Mission) -> Mission:
        """Persist if still at `mission.version`; returns the new version or raises."""
        ...


class TaskRepository(Protocol):
    async def add(self, task: Task) -> None: ...
    async def get(self, task_id: UUID, *, for_update: bool = False) -> Task: ...
    async def update(self, task: Task) -> Task: ...
    async def list_for_mission(self, mission_id: UUID) -> list[Task]: ...
    async def lock_next_ready(self) -> Task | None:
        """Lock the oldest READY task that no other transaction has locked."""
        ...


class ExecutionRepository(Protocol):
    async def add(self, execution: Execution, *, worker_id: str, lease_seconds: float) -> int:
        """Insert a newly claimed execution; returns its fence."""
        ...

    async def reclaim_expired(
        self, *, worker_id: str, lease_seconds: float
    ) -> tuple[Execution, int] | None:
        """Take over one RUNNING execution whose lease expired: new attempt, new fence."""
        ...

    async def get(self, execution_id: UUID) -> Execution: ...
    async def list_for_mission(self, mission_id: UUID) -> list[Execution]: ...
    async def update(self, execution: Execution, *, fence: int, lease_seconds: float) -> None:
        """Persist and renew the lease, only while `fence` still holds it; else LeaseLostError."""
        ...

    async def renew(self, execution_id: UUID, *, fence: int, lease_seconds: float) -> bool: ...
    async def release(self, execution_id: UUID, *, fence: int) -> None: ...
    async def mission_spend(self, mission_id: UUID) -> int: ...


class CheckpointRepository(Protocol):
    async def add(self, checkpoint: Checkpoint) -> None: ...
    async def latest(self, execution_id: UUID) -> Checkpoint | None: ...
    async def list(self, execution_id: UUID) -> list[Checkpoint]: ...


class ToolInvocationRepository(Protocol):
    async def get(self, idempotency_key: str) -> ToolInvocation | None: ...
    async def record_intent(self, invocation: ToolInvocation) -> ToolInvocation:
        """Insert as INTENDED, or count another invocation of an existing key."""
        ...

    async def record_outcome(self, idempotency_key: str, status: InvocationStatus) -> None: ...


class EventRepository(Protocol):
    async def append(
        self,
        event: DomainEvent,
        *,
        correlation_id: str | None,
        causation_id: UUID | None = None,
    ) -> EventRecord:
        """Append with the next per-aggregate sequence. Caller holds the aggregate's lock."""
        ...

    async def list_for_mission(self, mission_id: UUID) -> list[EventRecord]: ...
    async def list_for_aggregate(self, aggregate_id: UUID) -> list[EventRecord]: ...


class UnitOfWork(Protocol):
    agents: AgentRepository
    missions: MissionRepository
    tasks: TaskRepository
    executions: ExecutionRepository
    checkpoints: CheckpointRepository
    tool_invocations: ToolInvocationRepository
    events: EventRepository

    async def __aenter__(self) -> Self: ...
    async def __aexit__(self, *exc_info: object) -> None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> UnitOfWork: ...


class Clock(Protocol):
    def now(self) -> datetime: ...
