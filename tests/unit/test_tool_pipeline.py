import asyncio
from collections.abc import Mapping
from dataclasses import replace
from uuid import uuid4

import pytest

from forge.domain.common import JSONValue
from forge.domain.mission import RiskPolicy
from forge.domain.risk import RiskLevel
from forge.domain.tool import Idempotency, ToolDefinition, ToolResult
from forge.tools.base import ToolContext, ToolExecutionError
from forge.tools.pipeline import Stage, authorize, execute
from tests import factories

pytestmark = pytest.mark.anyio
CONTEXT = ToolContext("k", uuid4(), uuid4(), uuid4())


class FakeTool:
    def __init__(self, definition: ToolDefinition, *outcomes: str) -> None:
        self.definition = definition
        self._outcomes = list(outcomes)
        self.calls = 0

    async def invoke(self, arguments: Mapping[str, JSONValue], context: ToolContext) -> ToolResult:
        self.calls += 1
        outcome = self._outcomes.pop(0) if self._outcomes else "ok"
        if outcome == "hang":
            await asyncio.sleep(10)
        if outcome == "transient":
            raise ToolExecutionError("try again", retryable=True)
        if outcome == "permanent":
            raise ToolExecutionError("no", retryable=False)
        return ToolResult("ok")


def _setup(risk: RiskLevel = RiskLevel.LOW, max_risk: RiskLevel = RiskLevel.HIGH):  # type: ignore[no-untyped-def]
    agent, version = factories.agent_and_version()
    mission = factories.mission(risk_policy=RiskPolicy(max_risk))
    task = factories.task(mission_id=mission.id, agent_id=agent.id)
    tool = FakeTool(factories.tool_definition(risk=risk))
    return {"metrics.read": tool}, version, task, mission


@pytest.mark.parametrize(
    ("risk", "max_risk", "stage"),
    [
        (RiskLevel.LOW, RiskLevel.LOW, None),
        (RiskLevel.MEDIUM, RiskLevel.LOW, Stage.RISK),
        (RiskLevel.MEDIUM, RiskLevel.MEDIUM, Stage.POLICY),  # no allowing rule: deny
        (RiskLevel.HIGH, RiskLevel.HIGH, Stage.APPROVAL),  # no approval: wait for a human
        (RiskLevel.CRITICAL, RiskLevel.CRITICAL, Stage.POLICY),  # not enabled: deny
    ],
)
def test_risk_and_governance_stages_fail_closed_without_governance_state(
    risk: RiskLevel, max_risk: RiskLevel, stage: Stage | None
) -> None:
    tools, version, task, mission = _setup(risk, max_risk)

    decision = authorize(
        "metrics.read", tools=tools, version=version, task=task, mission=mission, arguments={}
    )

    assert decision.allowed is (stage is None)
    assert decision.stage == stage


def test_permission_requires_tool_in_catalog_agent_version_and_task() -> None:
    tools, version, task, mission = _setup()

    def check(**overrides: object) -> Stage | None:
        args: dict[str, object] = {
            "tools": tools,
            "version": version,
            "task": task,
            "mission": mission,
        }
        args.update(overrides)
        return authorize("metrics.read", **args).stage  # type: ignore[arg-type]

    assert check(tools={}) is Stage.PERMISSION
    assert check(version=replace(version, tools=())) is Stage.PERMISSION
    assert check(task=replace(task, allowed_tools=())) is Stage.PERMISSION
    assert check(task=replace(task, agent_id=uuid4())) is Stage.IDENTITY
    assert check() is None


async def test_transient_failures_of_repeatable_tools_are_retried() -> None:
    tool = FakeTool(
        factories.tool_definition(max_attempts=3, retry_initial_backoff_seconds=0),
        "transient",
        "transient",
    )

    outcome = await execute(tool, {}, CONTEXT)

    assert (outcome.attempts, outcome.failed, tool.calls) == (3, False, 3)


async def test_permanent_failures_are_not_retried() -> None:
    tool = FakeTool(factories.tool_definition(max_attempts=3), "permanent")

    outcome = await execute(tool, {}, CONTEXT)

    assert (outcome.attempts, outcome.failed, outcome.error_type) == (1, True, "ToolExecutionError")
    assert outcome.result is not None
    assert outcome.result.is_error


async def test_timeout_of_a_non_idempotent_tool_is_an_unknown_outcome() -> None:
    definition = factories.tool_definition(
        idempotency=Idempotency.NON_IDEMPOTENT, timeout_seconds=0.01
    )
    outcome = await execute(FakeTool(definition, "hang"), {}, CONTEXT)

    assert outcome.unknown
