from typing import Any

from forge.domain.agent import ModelRef
from forge.domain.cost import ModelPrice
from forge.inference.profiles import CachingMode, ModelProfile
from forge.inference.router import RoutingDecision, route

BIG = ModelRef("anthropic", "big")
SMALL = ModelRef("openai", "small")
PROFILES = {
    BIG: ModelProfile(
        BIG,
        200_000,
        8_000,
        ModelPrice(4_000_000, 20_000_000, 0, 0),
        True,
        CachingMode.EXPLICIT,
        512,
        3,
    ),
    SMALL: ModelProfile(
        SMALL,
        16_000,
        4_000,
        ModelPrice(250_000, 2_000_000, 0, 0),
        False,
        CachingMode.AUTOMATIC,
        1024,
        1,
    ),
}


def decide(candidates: tuple[ModelRef, ...] = (BIG, SMALL), **overrides: Any) -> RoutingDecision:
    args: dict[str, Any] = {
        "profiles": PROFILES,
        "available_providers": {"anthropic", "openai"},
        "required_context_tokens": 1_000,
        "reserved_output_tokens": 1_000,
        "needs_tools": False,
        "remaining_budget_micro_usd": 1_000_000,
    }
    args.update(overrides)
    return route(candidates, **args)


def test_first_eligible_candidate_in_operator_order_wins() -> None:
    decision = decide()

    assert decision.selected == BIG
    assert [c.reason for c in decision.candidates] == ["selected", "eligible"]


def test_each_rejection_is_explained() -> None:
    assert (
        decide(available_providers={"openai"}).candidates[0].reason
        == "provider 'anthropic' is not configured"
    )
    assert (
        decide((SMALL,), needs_tools=True).candidates[0].reason
        == "agent uses tools but the model does not"
    )
    assert (
        "window is 16000" in decide((SMALL,), required_context_tokens=15_001).candidates[0].reason
    )
    assert "micro-USD" in decide((BIG,), remaining_budget_micro_usd=3).candidates[0].reason
    assert decide((ModelRef("x", "y"),)).candidates[0].reason.startswith("no model profile")


def test_falls_back_down_the_list_and_reports_when_nothing_fits() -> None:
    assert decide(available_providers={"openai"}).selected == SMALL

    decision = decide(available_providers=set())

    assert decision.selected is None
    assert decision.explanation.startswith("no eligible model: anthropic/big")


def test_routing_is_deterministic() -> None:
    assert decide() == decide()
