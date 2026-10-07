"""Explicit context planning (docs/CONTEXT_ENGINEERING.md, ADR 0004).

A ContextPlan is the inspectable answer to "what did the model see, and why":
every block carries its source, version, priority, token estimate, cacheability,
and sensitivity, and every exclusion carries a reason.
"""

from dataclasses import dataclass
from enum import StrEnum


class ContextLayer(StrEnum):
    TOOLS = "tools"
    SYSTEM = "system"
    AGENT_IDENTITY = "agent_identity"
    POLICIES = "policies"
    MISSION = "mission"
    TRANSCRIPT = "transcript"


class Cacheability(StrEnum):
    STABLE = "stable"
    DYNAMIC = "dynamic"


class Sensitivity(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    SENSITIVE = "sensitive"  # never inside a cache-marked prefix (CONTEXT_ENGINEERING §12)


@dataclass(frozen=True, slots=True)
class ContextBlock:
    key: str
    layer: ContextLayer
    source: str
    version: str
    priority: int  # lower survives budget pressure longer
    text: str
    token_estimate: int
    cacheability: Cacheability
    sensitivity: Sensitivity = Sensitivity.INTERNAL


@dataclass(frozen=True, slots=True)
class Elision:
    key: str
    reason: str
    tokens_saved: int


@dataclass(frozen=True, slots=True)
class ContextPlan:
    blocks: tuple[ContextBlock, ...]  # in render order: stable first
    transcript_tokens: int
    budget_tokens: int
    elisions: tuple[Elision, ...]
    breakpoints: tuple[
        str, ...
    ]  # block keys followed by a cache breakpoint ("transcript" = last message)

    @property
    def total_tokens(self) -> int:
        return sum(block.token_estimate for block in self.blocks) + self.transcript_tokens

    @property
    def stable_tokens(self) -> int:
        return sum(b.token_estimate for b in self.blocks if b.cacheability is Cacheability.STABLE)

    def summary(self) -> dict[str, object]:
        """Identifiers and counts only: safe for events and telemetry."""
        return {
            "total_tokens": self.total_tokens,
            "stable_tokens": self.stable_tokens,
            "budget_tokens": self.budget_tokens,
            "blocks": [f"{b.key}@{b.version}" for b in self.blocks],
            "elided": [e.key for e in self.elisions],
            "breakpoints": list(self.breakpoints),
        }
