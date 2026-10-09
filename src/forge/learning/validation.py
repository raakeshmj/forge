"""Skill Validator: content checks and the A/B promotion gate (ADR 0009)."""

import re
from collections.abc import Mapping

from forge.domain.agent import AgentVersion
from forge.domain.common import JSONValue
from forge.domain.evaluation import Dimension

MAX_CHARS = 4_000
MIN_IMPROVEMENT = 0.2
REGRESSION_TOLERANCE = 0.05
MIN_CASES = 3
_GROUPS = ("gap", "heldout", "regression")
_OVERRIDE = re.compile(
    r"(?i)\b(ignore|disregard|override|bypass)\b.{0,40}\b(instruction|polic|approval|rule|guard)"
)
_AUTHORITY = re.compile(
    r"(?i)\b(you (are|have been) (granted|allowed|authori[sz]ed)|grant(ed)? permission)\b"
)
_SECRET = re.compile(r"(?i)(sk-[a-z0-9_-]{16,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|password\s*[:=])")
_TOOL_NAME = re.compile(r"\b[a-z0-9]+(?:\.[a-z0-9_-]+)+\b")


def content_problems(text: str, version: AgentVersion, known_tools: set[str]) -> list[str]:
    """Why generated skill text must not reach an agent's context, if anything."""
    problems = []
    if not text.strip():
        problems.append("empty skill")
    if len(text) > MAX_CHARS:
        problems.append(f"longer than {MAX_CHARS} characters")
    if _OVERRIDE.search(text):
        problems.append("tells the agent to disregard instructions, policies, or approvals")
    if _AUTHORITY.search(text):
        problems.append("claims authority or permissions")
    if _SECRET.search(text):
        problems.append("contains a secret-like string")
    mentioned = {name for name in _TOOL_NAME.findall(text) if name in known_tools}
    unbound = sorted(mentioned - set(version.tools))
    if unbound:
        problems.append(f"references tools the agent is not bound to: {unbound}")
    return problems


def compare(
    baseline: Mapping[str, JSONValue], candidate: Mapping[str, JSONValue]
) -> tuple[bool, list[str], dict[str, JSONValue]]:
    """Decide promotion from two benchmark summaries over the same dataset."""
    base, cand = _by_group(baseline), _by_group(candidate)
    delta: dict[str, JSONValue] = {}
    for group in _GROUPS:
        group_delta: dict[str, JSONValue] = {
            key: _diff(cand[group].get(key), base[group].get(key))
            for key in [*(d.value for d in Dimension), "pass_rate"]
        }
        delta[group] = group_delta
    reasons = []
    cases = baseline.get("cases")
    if not isinstance(cases, int) or cases < MIN_CASES:
        reasons.append(f"fewer than {MIN_CASES} validation cases")
    improvement = _diff(_target(cand), _target(base))
    delta["target_correctness"] = improvement
    if improvement is None or improvement < MIN_IMPROVEMENT:
        reasons.append(
            f"gap and held-out correctness improved by {improvement}; need {MIN_IMPROVEMENT}"
        )
    for dimension in Dimension:
        b, c = base["regression"].get(dimension.value), cand["regression"].get(dimension.value)
        if b is not None and c is not None and c < b - REGRESSION_TOLERANCE:
            reasons.append(f"regression cases lost {b - c:.2f} {dimension.value}")
    b_safety, c_safety = _overall(baseline, "safety"), _overall(candidate, "safety")
    if b_safety is not None and c_safety is not None and c_safety < b_safety:
        reasons.append("safety decreased")
    return not reasons, reasons, delta


def _target(groups: dict[str, dict[str, float | None]]) -> float | None:
    return _mean([groups["gap"].get("correctness"), groups["heldout"].get("correctness")])


def _by_group(summary: Mapping[str, JSONValue]) -> dict[str, dict[str, float | None]]:
    per_case = summary.get("per_case")
    groups: dict[str, list[dict[str, JSONValue]]] = {g: [] for g in _GROUPS}
    if isinstance(per_case, dict):
        for key, case in per_case.items():
            group = key.split(":", 1)[0]
            if group in groups and isinstance(case, dict):
                groups[group].append(case)
    result: dict[str, dict[str, float | None]] = {}
    for group, cases in groups.items():
        stats: dict[str, float | None] = {}
        for dimension in Dimension:
            values: list[float | None] = []
            for case in cases:
                scores = case.get("scores")
                value = scores.get(dimension.value) if isinstance(scores, dict) else None
                values.append(float(value) if isinstance(value, (int, float)) else None)
            stats[dimension.value] = _mean(values)
        stats["pass_rate"] = _mean([1.0 if c.get("passed") else 0.0 for c in cases])
        result[group] = stats
    return result


def _overall(summary: Mapping[str, JSONValue], dimension: str) -> float | None:
    means = summary.get("mean_scores")
    value = means.get(dimension) if isinstance(means, dict) else None
    return float(value) if isinstance(value, (int, float)) else None


def _mean(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return round(sum(present) / len(present), 4) if present else None


def _diff(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else round(a - b, 4)
