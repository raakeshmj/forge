import pytest

from forge.domain.checkpoint import AgentState
from forge.domain.model import ModelRequest, ToolResultBlock
from forge.inference.demo import DemoInvestigationModel
from forge.inference.pricing import DEMO_MODEL, PRICES
from forge.tools.demo import MetricsReadTool

pytestmark = pytest.mark.anyio


async def test_demo_model_requests_metrics_then_concludes_from_them() -> None:
    model, tool = DemoInvestigationModel(), MetricsReadTool()
    state = AgentState.initial("Why is checkout slow?")

    def request(s: AgentState) -> ModelRequest:
        return ModelRequest(DEMO_MODEL, "Investigate.", s.messages, (tool.definition,), 1024)

    first = await model.complete(request(state))
    (call,) = first.tool_uses
    assert call.name == "metrics.read"
    state = state.with_model_response(first).with_tool_result(
        ToolResultBlock(call.id, "checkout latency_p95_ms: 14:10=880")
    )
    second = await model.complete(request(state))

    assert second.tool_uses == ()
    assert "14:10=880" in second.text
    assert PRICES[DEMO_MODEL].cost_of(first.usage) > 0
    # Deterministic: the same request always yields the same response.
    assert await model.complete(request(state)) == second
