"""The only path from a model tool request to tool execution (CLAUDE.md §16, ARCHITECTURE §18).

identity -> permission -> risk -> policy -> execution -> audit. Audit is the
`tool.*` events the runtime commits. Stages owned by later phases deny: with no
Policy Engine or Approval Service yet, anything above LOW risk is refused.
"""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from forge.domain.agent import AgentVersion
from forge.domain.common import JSONValue
from forge.domain.mission import Mission
from forge.domain.risk import RiskLevel
from forge.domain.task import Task
from forge.domain.tool import ToolResult
from forge.tools.base import Tool, ToolContext, ToolExecutionError


class Stage(StrEnum):
    IDENTITY = "identity"
    PERMISSION = "permission"
    RISK = "risk"
    POLICY = "policy"


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    stage: Stage | None = None
    reason: str = ""


_ALLOW = Decision(allowed=True)

_UNAVAILABLE_GOVERNANCE = {
    RiskLevel.MEDIUM: "policy evaluation, which is not available yet",
    RiskLevel.HIGH: "human approval, which is not available yet",
    RiskLevel.CRITICAL: "administrator enablement; CRITICAL tools are disabled",
}


def authorize(
    tool_name: str,
    *,
    tools: Mapping[str, Tool],
    version: AgentVersion,
    task: Task,
    mission: Mission,
) -> Decision:
    if version.agent_id != task.agent_id or task.mission_id != mission.id:
        return Decision(False, Stage.IDENTITY, "execution identity does not match the task")
    tool = tools.get(tool_name)
    if tool is None:
        return Decision(False, Stage.PERMISSION, "tool is not available")
    if tool_name not in version.tools:
        return Decision(False, Stage.PERMISSION, "agent version is not bound to this tool")
    if tool_name not in task.allowed_tools:
        return Decision(False, Stage.PERMISSION, "task does not allow this tool")
    risk = tool.definition.risk
    if risk.exceeds(mission.risk_policy.max_risk):
        ceiling = mission.risk_policy.max_risk
        return Decision(False, Stage.RISK, f"{risk} risk exceeds the mission ceiling {ceiling}")
    if risk is not RiskLevel.LOW:
        return Decision(
            False, Stage.POLICY, f"{risk} risk requires {_UNAVAILABLE_GOVERNANCE[risk]}"
        )
    return _ALLOW


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    """`result` is None when the outcome is unknown: a non-repeatable tool timed out."""

    result: ToolResult | None
    attempts: int
    error_type: str | None = None

    @property
    def unknown(self) -> bool:
        return self.result is None

    @property
    def failed(self) -> bool:
        return self.error_type is not None


async def execute(
    tool: Tool, arguments: Mapping[str, JSONValue], context: ToolContext
) -> ToolOutcome:
    """Run an authorized call with its timeout, retrying only when repeating is safe."""
    definition = tool.definition
    retry = definition.retry
    attempt = 0
    while True:
        attempt += 1
        try:
            async with asyncio.timeout(definition.timeout_seconds):
                return ToolOutcome(await tool.invoke(arguments, context), attempt)
        except TimeoutError:
            if not definition.idempotency.safe_to_repeat:
                return ToolOutcome(None, attempt, "TimeoutError")
            error_type, retryable = "TimeoutError", True
            message = f"timed out after {definition.timeout_seconds}s"
        except ToolExecutionError as exc:
            error_type, retryable, message = type(exc).__name__, exc.retryable, str(exc)

        repeatable = retryable and definition.idempotency.safe_to_repeat
        if not repeatable or attempt >= retry.max_attempts:
            failure = ToolResult(f"Tool {definition.name} failed: {message}", is_error=True)
            return ToolOutcome(failure, attempt, error_type)
        await asyncio.sleep(retry.backoff_after(attempt))
