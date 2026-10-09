"""Skills and controlled learning (docs/DOMAIN_MODEL.md §9-10, ADR 0009).

Skill versions are immutable procedural knowledge; agents pin exact versions, so
promotion and rollback move an agent between immutable versions and never edit
behaviour in place. A learning cycle is the durable record of one pass through
failure -> gap -> candidate -> dataset -> validation -> promotion -> monitoring.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from forge.domain.common import DomainError, JSONValue, require_transition


@dataclass(frozen=True, slots=True)
class SkillRef:
    name: str
    version: int


class SkillStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"
    PROMOTED = "PROMOTED"
    DEPRECATED = "DEPRECATED"


_K = SkillStatus
SKILL_TRANSITIONS: dict[SkillStatus, frozenset[SkillStatus]] = {
    _K.CANDIDATE: frozenset({_K.VALIDATED, _K.REJECTED}),
    _K.VALIDATED: frozenset({_K.PROMOTED, _K.REJECTED}),
    _K.PROMOTED: frozenset({_K.DEPRECATED}),
}


@dataclass(frozen=True, slots=True)
class SkillVersion:
    name: str
    version: int
    capability: str
    instructions: str
    status: SkillStatus
    provenance: Mapping[str, JSONValue]  # source, generator, cycle, gap, failing evaluations
    parent_version: int | None
    created_at: datetime
    validation: Mapping[str, JSONValue] = field(default_factory=dict)
    row_version: int = 1

    @property
    def ref(self) -> SkillRef:
        return SkillRef(self.name, self.version)

    def transition(self, target: SkillStatus) -> "SkillVersion":
        require_transition(SKILL_TRANSITIONS, "skill", _NIL, self.status, target)
        return replace(self, status=target)


class CycleStatus(StrEnum):
    ANALYZED = "ANALYZED"
    NO_GAP = "NO_GAP"
    GAP_DETECTED = "GAP_DETECTED"
    NEEDS_DATASET = "NEEDS_DATASET"
    CANDIDATE_READY = "CANDIDATE_READY"
    VALIDATING = "VALIDATING"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"
    PROMOTED = "PROMOTED"
    ROLLED_BACK = "ROLLED_BACK"

    @property
    def is_terminal(self) -> bool:
        return self in {CycleStatus.NO_GAP, CycleStatus.REJECTED, CycleStatus.ROLLED_BACK}


_C = CycleStatus
CYCLE_TRANSITIONS: dict[CycleStatus, frozenset[CycleStatus]] = {
    _C.ANALYZED: frozenset({_C.NO_GAP, _C.GAP_DETECTED}),
    _C.GAP_DETECTED: frozenset({_C.NEEDS_DATASET, _C.CANDIDATE_READY, _C.REJECTED}),
    _C.CANDIDATE_READY: frozenset({_C.VALIDATING, _C.REJECTED}),
    _C.VALIDATING: frozenset({_C.VALIDATED, _C.REJECTED}),
    _C.VALIDATED: frozenset({_C.PROMOTED, _C.REJECTED}),
    _C.PROMOTED: frozenset({_C.ROLLED_BACK}),
}


@dataclass(frozen=True, slots=True)
class LearningCycle:
    id: UUID
    agent_id: UUID
    capability: str
    status: CycleStatus
    analysis: Mapping[str, JSONValue]
    gap: Mapping[str, JSONValue]
    skill: SkillRef | None
    dataset: str | None  # "benchmark@version"
    baseline_version_id: UUID | None
    candidate_version_id: UUID | None
    baseline_run_id: UUID | None
    candidate_run_id: UUID | None
    delta: Mapping[str, JSONValue]
    created_at: datetime
    updated_at: datetime
    row_version: int = 1

    def transition(self, target: CycleStatus, now: datetime, **changes: object) -> "LearningCycle":
        require_transition(CYCLE_TRANSITIONS, "learning cycle", self.id, self.status, target)
        return replace(self, status=target, updated_at=now, **changes)  # type: ignore[arg-type]


_NIL = UUID(int=0)


class LearningError(DomainError):
    """A learning step was refused (wrong state, failed gate). Not retryable as-is."""
