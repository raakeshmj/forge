import pytest

from forge.domain.checkpoint import AgentState
from forge.domain.common import DomainError
from forge.domain.cost import Usage
from forge.domain.model import ModelResponse, Role, TextBlock, ToolResultBlock, ToolUseBlock


def _response(*content: TextBlock | ToolUseBlock) -> ModelResponse:
    return ModelResponse(content=content, stop_reason="end_turn", usage=Usage())


def test_tool_calls_are_pending_until_each_has_a_result() -> None:
    a, b = ToolUseBlock("t1", "metrics.read", {}), ToolUseBlock("t2", "logs.read", {})
    state = AgentState.initial("objective").with_model_response(_response(a, b))

    assert state.pending_tool_uses() == (a, b)
    state = state.with_tool_result(ToolResultBlock("t1", "ok"))
    assert state.pending_tool_uses() == (b,)
    state = state.with_tool_result(ToolResultBlock("t2", "ok"))
    assert state.pending_tool_uses() == ()
    # Both results of one turn share a single user message.
    assert [m.role for m in state.messages] == [Role.USER, Role.ASSISTANT, Role.USER]
    assert state.turns == 1


def test_results_cannot_be_recorded_twice_or_for_unknown_calls() -> None:
    state = AgentState.initial("o").with_model_response(
        _response(ToolUseBlock("t1", "metrics.read", {}))
    )
    state = state.with_tool_result(ToolResultBlock("t1", "ok"))

    with pytest.raises(DomainError):
        state.with_tool_result(ToolResultBlock("t1", "again"))
    with pytest.raises(DomainError):
        state.with_tool_result(ToolResultBlock("nope", "x"))


def test_a_model_turn_cannot_start_while_tools_are_pending() -> None:
    state = AgentState.initial("o").with_model_response(
        _response(ToolUseBlock("t1", "metrics.read", {}))
    )

    with pytest.raises(DomainError):
        state.with_model_response(_response(TextBlock("done")))


def test_state_round_trips_through_json() -> None:
    state = (
        AgentState.initial("objective")
        .with_model_response(
            _response(TextBlock("checking"), ToolUseBlock("t1", "metrics.read", {"service": "x"}))
        )
        .with_tool_result(ToolResultBlock("t1", "p95=900ms", is_error=False))
        .with_model_response(_response(TextBlock("done")))
    )

    assert AgentState.from_json(state.to_json()) == state


def test_malformed_state_is_rejected() -> None:
    with pytest.raises(DomainError):
        AgentState.from_json({"messages": [{"role": "user", "content": [{"type": "bogus"}]}]})
