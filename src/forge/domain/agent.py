"""Agent identity and immutable agent versions (docs/DOMAIN_MODEL.md §5-6).

Versions carry instructions, model candidates and options, and tool bindings;
capability, skill, policy, memory, budget, and evaluation bindings arrive with
the phases that consume them.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from forge.domain.common import DomainError, JSONValue

# Request fields FORGE itself controls. Model options may tune provider behaviour but must
# never replace the planned context, tool set, or limits (ADR 0004).
RESERVED_MODEL_OPTIONS = frozenset(
    {
        "model",
        "messages",
        "system",
        "tools",
        "tool_choice",
        "max_tokens",
        "max_completion_tokens",
        "max_output_tokens",
        "prompt_cache_key",
        "stream",
        "cache_control",
        "input",
        "instructions",
    }
)


@dataclass(frozen=True, slots=True)
class ModelRef:
    provider: str
    name: str

    def __post_init__(self) -> None:
        if not self.provider or not self.name:
            raise DomainError("model provider and name are required")


@dataclass(frozen=True, slots=True)
class Agent:
    id: UUID
    name: str
    current_version: int
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise DomainError("agent name must not be empty")


@dataclass(frozen=True, slots=True)
class AgentVersion:
    id: UUID
    agent_id: UUID
    version: int
    role: str
    instructions: str
    model: ModelRef
    tools: tuple[str, ...]
    created_at: datetime
    fallback_models: tuple[ModelRef, ...] = ()
    capabilities: tuple[str, ...] = ()  # declared; measured competency arrives in Phase 6
    # Provider-specific parameters passed through untouched (CLAUDE.md §6).
    model_options: Mapping[str, JSONValue] = field(default_factory=dict)

    @property
    def model_candidates(self) -> tuple[ModelRef, ...]:
        """Models in the operator's order of preference (ADR 0004)."""
        return (self.model, *self.fallback_models)

    def __post_init__(self) -> None:
        reserved = sorted(RESERVED_MODEL_OPTIONS & set(self.model_options))
        if reserved:
            raise DomainError(f"model options may not set FORGE-controlled fields: {reserved}")
        if len(set(self.model_candidates)) != len(self.model_candidates):
            raise DomainError("model candidates must be distinct")
        if self.version < 1:
            raise DomainError("agent versions start at 1")
        if list(self.tools) != sorted(set(self.tools)):
            raise DomainError("tools must be sorted and unique")
