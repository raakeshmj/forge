from uuid import uuid4

import pytest

from forge.domain.checkpoint import AgentState
from forge.domain.model import ModelRequest, SystemBlock, ToolResultBlock
from forge.domain.risk import RiskLevel
from forge.inference.demo import DemoInvestigationModel
from forge.inference.profiles import DEMO_MODEL, PROFILES
from forge.tools.base import ToolContext
from forge.tools.demo import DeploymentRollbackTool, MetricsReadTool

pytestmark = pytest.mark.anyio
CONTEXT = ToolContext("k", uuid4(), uuid4(), uuid4())


async def test_demo_model_requests_metrics_then_concludes_from_them() -> None:
    model, tool = DemoInvestigationModel(), MetricsReadTool()
    state = AgentState.initial("Why is checkout slow?")

    def request(s: AgentState) -> ModelRequest:
        return ModelRequest(
            DEMO_MODEL, (SystemBlock("s", "Investigate."),), s.messages, (tool.definition,), 1024
        )

    first = await model.complete(request(state))
    (call,) = first.tool_uses
    assert call.name == "metrics.read"
    state = state.with_model_response(first).with_tool_result(
        ToolResultBlock(call.id, "checkout latency_p95_ms: 14:10=880")
    )
    second = await model.complete(request(state))

    assert second.tool_uses == ()
    assert "14:10=880" in second.text
    assert PROFILES[DEMO_MODEL].price.cost_of(first.usage) > 0
    # Deterministic: the same request always yields the same response.
    assert await model.complete(request(state)) == second


async def test_demo_model_requests_one_rollback_when_offered_and_reports_its_outcome() -> None:
    model, metrics, rollback = DemoInvestigationModel(), MetricsReadTool(), DeploymentRollbackTool()
    state = AgentState.initial("Why is checkout slow?")

    def request(s: AgentState) -> ModelRequest:
        tools = (metrics.definition, rollback.definition)
        return ModelRequest(
            DEMO_MODEL, (SystemBlock("s", "Investigate."),), s.messages, tools, 1024
        )

    first = await model.complete(request(state))
    (read,) = first.tool_uses
    series = await metrics.invoke(dict(read.arguments), CONTEXT)
    state = state.with_model_response(first).with_tool_result(
        ToolResultBlock(read.id, series.content)
    )
    second = await model.complete(request(state))
    (call,) = second.tool_uses
    assert (call.name, call.arguments["service"]) == ("deployment.rollback", "checkout")
    assert rollback.definition.risk is RiskLevel.HIGH
    outcome = await rollback.invoke(dict(call.arguments), CONTEXT)
    state = state.with_model_response(second).with_tool_result(
        ToolResultBlock(call.id, outcome.content)
    )
    third = await model.complete(request(state))

    assert third.tool_uses == ()  # one rollback only
    assert "Remediation: Rolled back checkout" in third.text
    assert "14:10=880" in third.text


async def test_a_denied_rollback_is_reported_not_retried() -> None:
    model, metrics, rollback = DemoInvestigationModel(), MetricsReadTool(), DeploymentRollbackTool()
    state = AgentState.initial("Why is checkout slow?")

    def request(s: AgentState) -> ModelRequest:
        tools = (metrics.definition, rollback.definition)
        return ModelRequest(
            DEMO_MODEL, (SystemBlock("s", "Investigate."),), s.messages, tools, 1024
        )

    first = await model.complete(request(state))
    series = await metrics.invoke(dict(first.tool_uses[0].arguments), CONTEXT)
    state = state.with_model_response(first).with_tool_result(
        ToolResultBlock(first.tool_uses[0].id, series.content)
    )
    second = await model.complete(request(state))
    denied = "Tool call denied at approval: denied by alice: freeze"
    state = state.with_model_response(second).with_tool_result(
        ToolResultBlock(second.tool_uses[0].id, denied, is_error=True)
    )
    third = await model.complete(request(state))

    assert third.tool_uses == ()
    assert f"Remediation: {denied}" in third.text
