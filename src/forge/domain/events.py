"""Immutable typed domain events (CLAUDE.md §14, docs/DOMAIN_MODEL.md §15).

Events are appended in the same transaction as the state change they describe
(ADR 0003). Payloads carry identifiers, counts, costs, and statuses; prompt and
tool content lives only in checkpoints.
"""

from dataclasses import asdict, dataclass, fields
from datetime import datetime
from enum import StrEnum
from typing import ClassVar
from uuid import UUID

from forge.domain.common import JSONObject, JSONValue
from forge.domain.cost import Usage


@dataclass(frozen=True, slots=True)
class DomainEvent:
    aggregate_type: ClassVar[str]
    event_type: ClassVar[str]

    aggregate_id: UUID
    mission_id: UUID | None

    def payload(self) -> JSONObject:
        return {
            f.name: _to_json(getattr(self, f.name))
            for f in fields(self)
            if f.name not in ("aggregate_id", "mission_id")
        }


@dataclass(frozen=True, slots=True)
class EventRecord:
    """A persisted event: the domain event plus its envelope."""

    id: UUID
    event_type: str
    aggregate_type: str
    aggregate_id: UUID
    sequence: int
    mission_id: UUID | None
    payload: JSONObject
    correlation_id: str | None
    causation_id: UUID | None
    occurred_at: datetime


def _to_json(value: object) -> JSONValue:
    match value:
        case None | bool() | int() | float() | str():
            return value
        case StrEnum():
            return value.value
        case UUID():
            return str(value)
        case datetime():
            return value.isoformat()
        case tuple() | list():
            return [_to_json(item) for item in value]
        case Usage():
            return {key: _to_json(item) for key, item in asdict(value).items()}
    raise TypeError(f"cannot serialise {type(value).__name__} into an event payload")


# --- Agent registry -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AgentRegistered(DomainEvent):
    aggregate_type = "agent"
    event_type = "agent.registered"
    name: str
    version: int
    model_provider: str
    model_name: str
    tools: tuple[str, ...]


# --- Mission ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MissionCreated(DomainEvent):
    aggregate_type = "mission"
    event_type = "mission.created"
    agent_id: UUID
    budget_micro_usd: int
    max_risk: str
    deadline: datetime | None


@dataclass(frozen=True, slots=True)
class MissionPlanned(DomainEvent):
    aggregate_type = "mission"
    event_type = "mission.planned"
    task_count: int


@dataclass(frozen=True, slots=True)
class MissionStarted(DomainEvent):
    aggregate_type = "mission"
    event_type = "mission.started"


@dataclass(frozen=True, slots=True)
class MissionCompleted(DomainEvent):
    aggregate_type = "mission"
    event_type = "mission.completed"
    cost_micro_usd: int


@dataclass(frozen=True, slots=True)
class MissionFailed(DomainEvent):
    aggregate_type = "mission"
    event_type = "mission.failed"
    failure_kind: str
    cost_micro_usd: int


@dataclass(frozen=True, slots=True)
class MissionCancelled(DomainEvent):
    aggregate_type = "mission"
    event_type = "mission.cancelled"


# --- Task ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TaskCreated(DomainEvent):
    aggregate_type = "task"
    event_type = "task.created"
    agent_id: UUID
    allowed_tools: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TaskStarted(DomainEvent):
    aggregate_type = "task"
    event_type = "task.started"
    execution_id: UUID


@dataclass(frozen=True, slots=True)
class TaskCompleted(DomainEvent):
    aggregate_type = "task"
    event_type = "task.completed"


@dataclass(frozen=True, slots=True)
class TaskFailed(DomainEvent):
    aggregate_type = "task"
    event_type = "task.failed"
    failure_kind: str


@dataclass(frozen=True, slots=True)
class TaskCancelled(DomainEvent):
    aggregate_type = "task"
    event_type = "task.cancelled"


# --- Execution -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AgentStarted(DomainEvent):
    aggregate_type = "execution"
    event_type = "agent.started"
    attempt: int
    worker_id: str
    agent_id: UUID
    agent_version_id: UUID


@dataclass(frozen=True, slots=True)
class ModelCalled(DomainEvent):
    aggregate_type = "execution"
    event_type = "model.called"
    turn: int
    provider: str
    model: str
    attempts: int
    stop_reason: str
    usage: Usage
    cost_micro_usd: int


@dataclass(frozen=True, slots=True)
class ModelFailed(DomainEvent):
    aggregate_type = "execution"
    event_type = "model.failed"
    turn: int
    attempt: int
    error_type: str
    retryable: bool


@dataclass(frozen=True, slots=True)
class ToolCalled(DomainEvent):
    aggregate_type = "execution"
    event_type = "tool.called"
    turn: int
    tool_use_id: str
    tool: str
    tool_version: str
    idempotency_key: str
    invocation: int  # 1 on first call; >1 when re-executed after a crash


@dataclass(frozen=True, slots=True)
class ToolCompleted(DomainEvent):
    aggregate_type = "execution"
    event_type = "tool.completed"
    tool_use_id: str
    tool: str
    is_error: bool
    attempts: int
    cost_micro_usd: int


@dataclass(frozen=True, slots=True)
class ToolFailed(DomainEvent):
    aggregate_type = "execution"
    event_type = "tool.failed"
    tool_use_id: str
    tool: str
    error_type: str
    attempts: int


@dataclass(frozen=True, slots=True)
class ToolDenied(DomainEvent):
    aggregate_type = "execution"
    event_type = "tool.denied"
    tool_use_id: str
    tool: str
    stage: str
    reason: str


@dataclass(frozen=True, slots=True)
class CheckpointCreated(DomainEvent):
    aggregate_type = "execution"
    event_type = "checkpoint.created"
    sequence: int
    state_version: int


@dataclass(frozen=True, slots=True)
class AgentCompleted(DomainEvent):
    aggregate_type = "execution"
    event_type = "agent.completed"
    turns: int
    usage: Usage
    cost_micro_usd: int


@dataclass(frozen=True, slots=True)
class AgentFailed(DomainEvent):
    aggregate_type = "execution"
    event_type = "agent.failed"
    failure_kind: str
    detail: str


@dataclass(frozen=True, slots=True)
class AgentCancelled(DomainEvent):
    aggregate_type = "execution"
    event_type = "agent.cancelled"
