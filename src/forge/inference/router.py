"""Deterministic model routing (ARCHITECTURE §9, ADR 0004).

Walks the agent version's candidates in operator order and selects the first
that can serve the execution. Every alternative is recorded with its reason.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from forge.domain.agent import ModelRef
from forge.domain.cost import Usage
from forge.inference.profiles import ModelProfile


@dataclass(frozen=True, slots=True)
class CandidateAssessment:
    model: ModelRef
    eligible: bool
    reason: str
    min_call_cost_micro_usd: int | None = None


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    selected: ModelRef | None
    candidates: tuple[CandidateAssessment, ...]

    @property
    def explanation(self) -> str:
        if self.selected is None:
            return "no eligible model: " + "; ".join(
                f"{c.model.provider}/{c.model.name}: {c.reason}" for c in self.candidates
            )
        return f"selected {self.selected.provider}/{self.selected.name} (first eligible in order)"


def route(
    candidates: Sequence[ModelRef],
    *,
    profiles: Mapping[ModelRef, ModelProfile],
    available_providers: Collection[str],
    required_context_tokens: int,
    reserved_output_tokens: int,
    needs_tools: bool,
    remaining_budget_micro_usd: int,
) -> RoutingDecision:
    assessments: list[CandidateAssessment] = []
    selected: ModelRef | None = None
    for model in candidates:
        assessment = _assess(
            model,
            profiles.get(model),
            available_providers=available_providers,
            required_context_tokens=required_context_tokens,
            reserved_output_tokens=reserved_output_tokens,
            needs_tools=needs_tools,
            remaining_budget_micro_usd=remaining_budget_micro_usd,
        )
        if assessment.eligible and selected is None:
            selected = model
            assessment = CandidateAssessment(
                model, True, "selected", assessment.min_call_cost_micro_usd
            )
        assessments.append(assessment)
    return RoutingDecision(selected, tuple(assessments))


def _assess(
    model: ModelRef,
    profile: ModelProfile | None,
    *,
    available_providers: Collection[str],
    required_context_tokens: int,
    reserved_output_tokens: int,
    needs_tools: bool,
    remaining_budget_micro_usd: int,
) -> CandidateAssessment:
    if profile is None:
        return CandidateAssessment(model, False, "no model profile (unknown capabilities or price)")
    if model.provider not in available_providers:
        return CandidateAssessment(model, False, f"provider {model.provider!r} is not configured")
    if needs_tools and not profile.supports_tools:
        return CandidateAssessment(model, False, "agent uses tools but the model does not")
    output = min(reserved_output_tokens, profile.max_output_tokens)
    if required_context_tokens + output > profile.context_window_tokens:
        return CandidateAssessment(
            model,
            False,
            f"needs {required_context_tokens + output} tokens; window is "
            f"{profile.context_window_tokens}",
        )
    min_cost = profile.price.cost_of(Usage(input_tokens=required_context_tokens, output_tokens=1))
    if min_cost > remaining_budget_micro_usd:
        return CandidateAssessment(
            model,
            False,
            f"one call costs at least {min_cost} micro-USD; {remaining_budget_micro_usd} remain",
            min_cost,
        )
    return CandidateAssessment(model, True, "eligible", min_cost)
