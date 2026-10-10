"""Query-side read models for the control plane (ADR 0012).

Aggregates computed by the system of record. They are derived and disposable: no
command ever reads them to make a decision.
"""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class MissionSummary:
    id: UUID
    objective: str
    status: str
    created_at: datetime
    updated_at: datetime
    cost_micro_usd: int
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    tasks_total: int
    tasks_completed: int


@dataclass(frozen=True, slots=True)
class ModelUsage:
    provider: str
    model: str
    calls: int
    latency_p50_ms: float
    latency_p95_ms: float
    latency_p99_ms: float
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    cost_micro_usd: int
    cache_savings_micro_usd: int

    @property
    def cache_hit_rate(self) -> float:
        """Share of prompt tokens served from the provider's cache."""
        prompt = self.input_tokens + self.cache_read_input_tokens + self.cache_creation_input_tokens
        return self.cache_read_input_tokens / prompt if prompt else 0.0


@dataclass(frozen=True, slots=True)
class ToolUsage:
    tool: str
    calls: int
    errors: int  # completed with an error result
    failures: int  # tool.failed
    denials: int  # tool.denied by the pipeline
    reinvocations: int  # calls re-executed after a crash (invocation > 1)


@dataclass(frozen=True, slots=True)
class CostShare:
    key: str  # agent name, model, or ISO day
    cost_micro_usd: int
    executions: int


@dataclass(frozen=True, slots=True)
class Count:
    key: str
    count: int


@dataclass(frozen=True, slots=True)
class Overview:
    since: datetime | None
    missions_by_status: tuple[Count, ...]
    executions_by_status: tuple[Count, ...]
    failures_by_kind: tuple[Count, ...]
    models: tuple[ModelUsage, ...]
    tools: tuple[ToolUsage, ...]
    cost_by_agent: tuple[CostShare, ...]
    cost_by_day: tuple[CostShare, ...]
    pending_approvals: int

    @property
    def total_cost_micro_usd(self) -> int:
        return sum(share.cost_micro_usd for share in self.cost_by_agent)


@dataclass(frozen=True, slots=True)
class DimensionMean:
    dimension: str
    mean: float
    samples: int


@dataclass(frozen=True, slots=True)
class AgentUsage:
    agent_id: UUID
    executions: int
    cost_micro_usd: int
    models: tuple[ModelUsage, ...]
    failures_by_kind: tuple[Count, ...]  # failure clusters by recorded kind
    dimensions: tuple[DimensionMean, ...]  # mean task-scope evaluation scores
    findings: tuple[Count, ...] = field(default=())  # most frequent evaluation finding codes
