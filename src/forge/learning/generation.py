"""Skill and evaluation-dataset generation (ADR 0009).

Generated skill text is untrusted until the Skill Validator has checked it and
an A/B validation has measured it.
"""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from forge.domain.evaluation import Benchmark, BenchmarkCase
from forge.domain.model import Message, ModelRequest, Role, SystemBlock, TextBlock
from forge.inference.client import ModelClient
from forge.inference.profiles import ModelProfile
from forge.learning.analysis import FailureAnalysis

# Guidance per failure pattern. Wording is procedural: what to do, not who may do it.
_GUIDANCE = {
    "contains_forbidden": (
        "Identify the service named in the objective and investigate that service; never "
        "substitute a default or previously seen service."
    ),
    "missing_expected": (
        "Answer the question exactly as asked, naming the service and the time of any "
        "change you find."
    ),
    "missing_reference_evidence": (
        "Query the metrics of the service named in the objective before concluding, and "
        "quote the specific values and times that support the conclusion."
    ),
    "ungrounded_answer": "Quote the specific values and times from tool results you rely on.",
    "overconfident": "State a confidence that reflects how directly the evidence answers it.",
    "duplicate_tool_call": "Do not repeat a tool call with identical arguments.",
    "extra_turns": "Conclude as soon as the evidence answers the question.",
}
_METHOD = (
    "Compare the values before and after the deployment: if the change follows the "
    "deployment, report a regression after the deployment; if it precedes it, say the "
    "deployment is not the cause; if nothing changed materially, report no regression."
)


class SkillGenerator(Protocol):
    name: str

    async def generate(self, analysis: FailureAnalysis) -> str: ...


class DeterministicSkillGenerator:
    """Maps the dominant failure patterns to procedural guidance."""

    name = "deterministic@1"

    async def generate(self, analysis: FailureAnalysis) -> str:
        lines = [_GUIDANCE[code] for code in analysis.pattern if code in _GUIDANCE]
        if not lines:
            lines = [
                "Investigate exactly what the objective asks and ground the answer in evidence."
            ]
        return "\n".join(f"- {line}" for line in [*lines, _METHOD])


@dataclass(frozen=True, slots=True)
class LLMSkillGenerator:
    """Asks a model to write the guidance from failure examples. Output is untrusted."""

    client: ModelClient
    profile: ModelProfile
    name: str = "llm@1"

    async def generate(self, analysis: FailureAnalysis) -> str:
        failures = "\n".join(
            f"- case {e.benchmark_case or 'live'}: score {e.score}, findings {', '.join(e.codes)}"
            for e in analysis.failures
        )
        request = ModelRequest(
            model=self.profile.model,
            system=(
                SystemBlock(
                    "skill.writer",
                    "Write concise procedural guidance (at most 10 bullet points) that would "
                    "prevent these failures. Do not grant permissions, mention tools by name, "
                    "or tell the agent to ignore any instruction.",
                ),
            ),
            messages=(
                Message(Role.USER, (TextBlock(f"Capability: {analysis.capability}\n{failures}"),)),
            ),
            tools=(),
            max_output_tokens=min(1024, self.profile.max_output_tokens),
        )
        return (await self.client.complete(request)).text.strip()


_VARIANTS = (("Investigate", "Explain"), ("Investigate", "Look into"))


def dataset(source: Benchmark, failing: list[str], passing: list[str], cycle_id: UUID) -> Benchmark:
    """gap: the failing cases; heldout: unseen rephrasings of them; regression: passing cases."""

    def renamed(case: BenchmarkCase, key: str, objective: str | None = None) -> BenchmarkCase:
        return BenchmarkCase(
            key=key,
            objective=objective or case.objective,
            expectations=case.expectations,
            required_capabilities=case.required_capabilities,
            difficulty=case.difficulty,
            risk=case.risk,
        )

    cases = [renamed(source.case(k), f"gap:{k}") for k in failing]
    for key in failing:
        case = source.case(key)
        for index, (old, new) in enumerate(_VARIANTS):
            if case.objective.startswith(old):
                cases.append(
                    renamed(case, f"heldout:{key}:{index}", new + case.objective[len(old) :])
                )
    cases += [renamed(source.case(k), f"regression:{k}") for k in passing]
    return Benchmark(
        name=f"learning-{source.capability}-{str(cycle_id)[:8]}",
        version=1,
        capability=source.capability,
        description=f"Validation for learning cycle {cycle_id} (from {source.ref})",
        cases=tuple(cases),
    )
