"""HTTP request and response shapes. Validation happens here; domain types never see raw JSON."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from forge.domain.events import EventRecord
from forge.domain.execution import Execution
from forge.domain.mission import Mission
from forge.domain.risk import RiskLevel
from forge.domain.task import Task

_Name = Field(min_length=1, max_length=200)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelRefIn(_Strict):
    provider: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)


class AgentCreate(_Strict):
    name: str = _Name
    role: str = Field(min_length=1, max_length=200)
    instructions: str = Field(min_length=1, max_length=50_000)
    model: ModelRefIn
    fallback_models: list[ModelRefIn] = Field(default_factory=list, max_length=5)
    model_options: dict[str, Any] = Field(default_factory=dict)
    tools: list[str] = Field(default_factory=list, max_length=100)


class AgentCreated(BaseModel):
    agent_id: UUID
    version: int
    version_id: UUID


class ExecutionPolicyIn(_Strict):
    max_steps: int = Field(default=8, ge=1, le=100)
    model_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    model_max_attempts: int = Field(default=3, ge=1, le=10)


class MissionCreate(_Strict):
    objective: str = Field(min_length=1, max_length=10_000)
    agent_id: UUID
    budget_micro_usd: int = Field(gt=0)
    deadline: AwareDatetime | None = None
    constraints: list[str] = Field(default_factory=list, max_length=50)
    success_criteria: list[str] = Field(default_factory=list, max_length=50)
    required_capabilities: list[str] = Field(default_factory=list, max_length=50)
    allowed_tools: list[str] | None = Field(default=None, max_length=100)
    max_risk: RiskLevel = RiskLevel.LOW
    execution_policy: ExecutionPolicyIn = Field(default_factory=ExecutionPolicyIn)


class TaskView(BaseModel):
    id: UUID
    status: str
    agent_id: UUID
    allowed_tools: list[str]

    @classmethod
    def of(cls, task: Task) -> "TaskView":
        return cls(
            id=task.id,
            status=task.status.value,
            agent_id=task.agent_id,
            allowed_tools=list(task.allowed_tools),
        )


class ExecutionView(BaseModel):
    id: UUID
    task_id: UUID
    status: str
    attempt: int
    agent_version_id: UUID
    model_provider: str
    model_name: str
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    cost_micro_usd: int
    cache_hit_rate: float
    output: str | None
    failure_kind: str | None
    failure_detail: str | None
    started_at: datetime
    completed_at: datetime | None

    @classmethod
    def of(cls, execution: Execution) -> "ExecutionView":
        return cls(
            id=execution.id,
            task_id=execution.task_id,
            status=execution.status.value,
            attempt=execution.attempt,
            agent_version_id=execution.agent_version_id,
            model_provider=execution.model.provider,
            model_name=execution.model.name,
            input_tokens=execution.usage.input_tokens,
            output_tokens=execution.usage.output_tokens,
            cache_read_input_tokens=execution.usage.cache_read_input_tokens,
            cache_creation_input_tokens=execution.usage.cache_creation_input_tokens,
            cost_micro_usd=execution.cost_micro_usd,
            cache_hit_rate=_cache_hit_rate(execution),
            output=execution.output,
            failure_kind=None if execution.failure_kind is None else execution.failure_kind.value,
            failure_detail=execution.failure_detail,
            started_at=execution.started_at,
            completed_at=execution.completed_at,
        )


class MissionView(BaseModel):
    id: UUID
    status: str
    objective: str
    budget_micro_usd: int
    deadline: datetime | None
    max_risk: str
    correlation_id: str
    created_at: datetime
    updated_at: datetime
    tasks: list[TaskView]
    executions: list[ExecutionView]

    @classmethod
    def of(cls, mission: Mission, tasks: list[Task], executions: list[Execution]) -> "MissionView":
        return cls(
            id=mission.id,
            status=mission.status.value,
            objective=mission.objective,
            budget_micro_usd=mission.budget.limit_micro_usd,
            deadline=mission.deadline,
            max_risk=mission.risk_policy.max_risk.value,
            correlation_id=mission.correlation_id,
            created_at=mission.created_at,
            updated_at=mission.updated_at,
            tasks=[TaskView.of(task) for task in tasks],
            executions=[ExecutionView.of(execution) for execution in executions],
        )


class EventView(BaseModel):
    id: UUID
    event_type: str
    aggregate_type: str
    aggregate_id: UUID
    sequence: int
    payload: dict[str, Any]
    correlation_id: str | None
    causation_id: UUID | None
    occurred_at: datetime

    @classmethod
    def of(cls, record: EventRecord) -> "EventView":
        return cls(
            id=record.id,
            event_type=record.event_type,
            aggregate_type=record.aggregate_type,
            aggregate_id=record.aggregate_id,
            sequence=record.sequence,
            payload=record.payload,
            correlation_id=record.correlation_id,
            causation_id=record.causation_id,
            occurred_at=record.occurred_at,
        )


def _cache_hit_rate(execution: Execution) -> float:
    """Share of input tokens served from the provider's prompt cache."""
    usage = execution.usage
    total = usage.input_tokens + usage.cache_read_input_tokens + usage.cache_creation_input_tokens
    return round(usage.cache_read_input_tokens / total, 4) if total else 0.0
