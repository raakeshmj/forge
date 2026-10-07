from dataclasses import replace

import pytest

from forge.domain.checkpoint import AgentState
from forge.domain.context import Cacheability, Sensitivity
from forge.domain.cost import ModelPrice, Usage
from forge.domain.model import ModelResponse, TextBlock, ToolResultBlock, ToolUseBlock
from forge.inference.context import (
    TRANSCRIPT_KEY,
    ContextExceededError,
    PlanningInputs,
    build_request,
    plan_cache,
    plan_context,
)
from forge.inference.profiles import CachingMode, ModelProfile
from tests import factories

PRICE = ModelPrice(1_000_000, 5_000_000, 100_000, 1_250_000)


def profile(caching: CachingMode = CachingMode.EXPLICIT, minimum: int = 0) -> ModelProfile:
    ref = factories.agent_and_version()[1].model
    return ModelProfile(ref, 100_000, 4_096, PRICE, True, caching, minimum, 2)


def inputs(state: AgentState, **mission: object) -> PlanningInputs:
    _, version = factories.agent_and_version(instructions="Investigate carefully. " * 40)
    return PlanningInputs(
        version, factories.mission(**mission), (factories.tool_definition(),), state.messages
    )


def turn(state: AgentState, call_id: str, result: str) -> AgentState:
    response = ModelResponse(
        (TextBlock("checking"), ToolUseBlock(call_id, "metrics.read", {})), "tool_use", Usage()
    )
    return state.with_model_response(response).with_tool_result(ToolResultBlock(call_id, result))


def test_stable_blocks_come_first_ordered_by_sharing_scope() -> None:
    plan, _ = plan_context(
        inputs(AgentState.initial("o"), constraints=("read only",)), budget_tokens=10_000
    )

    assert [b.key for b in plan.blocks] == [
        "tools",
        "system.platform",
        "agent.identity",
        "policies.mission",
        "mission.framing",
    ]
    assert {b.cacheability for b in plan.blocks} == {Cacheability.STABLE}
    assert plan.transcript_tokens > 0


def test_mission_framing_is_omitted_when_there_is_nothing_to_say() -> None:
    plan, _ = plan_context(inputs(AgentState.initial("o")), budget_tokens=10_000)

    assert "mission.framing" not in [b.key for b in plan.blocks]


def test_planning_is_deterministic_and_the_prefix_is_stable_across_turns() -> None:
    first_turn = inputs(AgentState.initial("o"))
    later_turn = replace(first_turn, messages=turn(AgentState.initial("o"), "c1", "data").messages)

    def request(i: PlanningInputs):  # type: ignore[no-untyped-def]
        plan, messages = plan_context(i, budget_tokens=10_000)
        plan, hints = plan_cache(plan, profile())
        return build_request(
            plan, hints, messages, inputs=i, profile=profile(), max_output_tokens=1024
        )

    assert request(first_turn) == request(first_turn)
    assert request(first_turn).system == request(later_turn).system
    assert request(first_turn).tools == request(later_turn).tools


def test_oldest_tool_results_are_elided_first_and_the_latest_turn_is_kept() -> None:
    state = AgentState.initial("o")
    for index in range(4):
        state = turn(state, f"c{index}", "x" * 4_000)
    base = inputs(state)
    full, _ = plan_context(base, budget_tokens=100_000)

    plan, messages = plan_context(base, budget_tokens=full.total_tokens - 1_500)

    assert [e.key for e in plan.elisions] == ["c0", "c1"]
    assert plan.total_tokens <= plan.budget_tokens
    results = [b for m in messages for b in m.content if isinstance(b, ToolResultBlock)]
    assert [r.content.startswith("[elided") for r in results] == [True, True, False, False]
    assert plan_context(base, budget_tokens=full.total_tokens - 1_500) == (plan, messages)


def test_context_that_cannot_fit_is_an_error_not_a_silent_truncation() -> None:
    with pytest.raises(ContextExceededError):
        plan_context(inputs(AgentState.initial("o")), budget_tokens=10)


def test_explicit_caching_marks_each_sharing_scope_and_the_transcript() -> None:
    plan, _ = plan_context(inputs(AgentState.initial("o")), budget_tokens=10_000)

    planned, hints = plan_cache(plan, profile())

    assert planned.breakpoints == (
        "system.platform",
        "agent.identity",
        "policies.mission",
        TRANSCRIPT_KEY,
    )
    assert hints.last_message


def test_breakpoints_respect_the_minimum_cacheable_prefix() -> None:
    plan, _ = plan_context(inputs(AgentState.initial("o")), budget_tokens=10_000)
    identity_prefix = sum(b.token_estimate for b in plan.blocks[:3])

    planned, _ = plan_cache(plan, profile(minimum=identity_prefix))

    assert "system.platform" not in planned.breakpoints
    assert planned.breakpoints[0] == "agent.identity"


def test_no_breakpoint_is_placed_at_or_after_sensitive_context() -> None:
    plan, _ = plan_context(inputs(AgentState.initial("o")), budget_tokens=10_000)
    blocks = list(plan.blocks)
    blocks[3] = replace(blocks[3], sensitivity=Sensitivity.SENSITIVE)  # policies

    planned, hints = plan_cache(replace(plan, blocks=tuple(blocks)), profile())

    assert planned.breakpoints == ("system.platform", "agent.identity")
    assert not hints.last_message


def test_automatic_caching_uses_a_stable_affinity_key_unless_context_is_sensitive() -> None:
    base = inputs(AgentState.initial("o"))
    later_messages = turn(AgentState.initial("o"), "c", "d").messages
    plan, _ = plan_context(base, budget_tokens=10_000)
    later, _ = plan_context(replace(base, messages=later_messages), budget_tokens=10_000)
    automatic = profile(CachingMode.AUTOMATIC)

    _, hints = plan_cache(plan, automatic)
    sensitive = replace(
        plan,
        blocks=(replace(plan.blocks[0], sensitivity=Sensitivity.SENSITIVE), *plan.blocks[1:]),
    )

    assert hints.key is not None
    assert plan_cache(later, automatic)[1].key == hints.key  # stable across turns
    assert plan_cache(sensitive, automatic)[1].key is None
    assert plan_cache(plan, profile(CachingMode.NONE))[1].key is None


def test_request_carries_breakpoints_output_cap_and_provider_options() -> None:
    base = inputs(AgentState.initial("o"))
    base = replace(
        base, version=replace(base.version, model_options={"output_config": {"effort": "high"}})
    )
    plan, messages = plan_context(base, budget_tokens=10_000)
    plan, hints = plan_cache(plan, profile())

    request = build_request(
        plan, hints, messages, inputs=base, profile=profile(), max_output_tokens=99_999
    )

    assert [b.key for b in request.system if b.cache_breakpoint] == [
        "system.platform",
        "agent.identity",
        "policies.mission",
    ]
    assert request.max_output_tokens == 4_096
    assert request.provider_options == {"output_config": {"effort": "high"}}
