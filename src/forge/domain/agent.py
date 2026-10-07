"""Agent identity and immutable agent versions (docs/DOMAIN_MODEL.md §5-6).

Phase 1 versions carry instructions, model, and tool bindings; capability,
skill, policy, memory, budget, and evaluation bindings arrive with the phases
that consume them.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from forge.domain.common import DomainError


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

    def __post_init__(self) -> None:
        if self.version < 1:
            raise DomainError("agent versions start at 1")
        if list(self.tools) != sorted(set(self.tools)):
            raise DomainError("tools must be sorted and unique")
