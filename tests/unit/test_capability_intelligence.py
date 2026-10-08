from datetime import timedelta
from uuid import UUID, uuid4

import pytest

from forge.domain.capability import (
    Candidate,
    Capability,
    CapabilityGraph,
    Competency,
    Observation,
    Reliability,
    estimate,
    fitness,
    prior,
)
from tests.factories import NOW

AGENT = uuid4()
GRAPH = CapabilityGraph(
    {
        "incident-response": Capability("incident-response"),
        "observability": Capability("observability", parent="incident-response"),
        "postgres": Capability("postgres", parent="databases", prerequisites=("sql",)),
        "sql": Capability("sql"),
    }
)


def obs(
    score: float, *, days_ago: float = 0, confidence: float = 1.0, difficulty: int = 1
) -> Observation:
    return Observation(uuid4(), score, confidence, NOW - timedelta(days=days_ago), difficulty)


def test_no_evidence_is_maximally_uncertain_not_zero() -> None:
    competency = prior(AGENT, "x")

    assert competency.estimated_score == 0.5
    assert competency.uncertainty == pytest.approx(0.2887, abs=1e-4)
    assert competency.sample_count == 0


def test_evidence_moves_the_estimate_and_shrinks_uncertainty() -> None:
    few = estimate(AGENT, "x", [obs(1.0)] * 2, NOW)
    many = estimate(AGENT, "x", [obs(1.0)] * 20, NOW)

    assert few.estimated_score < many.estimated_score
    assert many.uncertainty < few.uncertainty < prior(AGENT, "x").uncertainty
    assert many.sample_count == 20


def test_old_low_confidence_and_easy_evidence_counts_for_less() -> None:
    fresh = estimate(AGENT, "x", [obs(0.0)], NOW)
    stale = estimate(AGENT, "x", [obs(0.0, days_ago=28)], NOW)  # two half-lives
    unsure = estimate(AGENT, "x", [obs(0.0, confidence=0.25)], NOW)
    hard = estimate(AGENT, "x", [obs(1.0, difficulty=5)], NOW)
    easy = estimate(AGENT, "x", [obs(1.0, difficulty=1)], NOW)

    assert stale.effective_samples == pytest.approx(0.25)
    assert stale.estimated_score > fresh.estimated_score
    assert unsure.estimated_score > fresh.estimated_score
    assert hard.estimated_score > easy.estimated_score


def test_trend_detects_regression() -> None:
    history = [obs(1.0, days_ago=10 - i) for i in range(5)] + [
        obs(0.0, days_ago=5 - i) for i in range(5)
    ]

    competency = estimate(AGENT, "x", history, NOW)

    assert competency.recent_trend == -1.0
    assert len(competency.supporting_evaluations) == 10


def test_graph_eligibility_follows_descendants_and_prerequisites() -> None:
    assert GRAPH.serves({"observability"}, "incident-response")
    assert not GRAPH.serves({"incident-response"}, "observability")
    assert not GRAPH.serves({"postgres"}, "postgres")  # missing prerequisite sql
    assert GRAPH.serves({"postgres", "sql"}, "postgres")
    assert GRAPH.serves({"anything"}, "anything")  # unknown names are roots
    assert GRAPH.attribution({"observability"}) == {"observability": 1.0, "incident-response": 0.5}


def competency(score: float, samples: int, *, calibration: float | None = None) -> Competency:
    return estimate(
        AGENT,
        "x",
        [Observation(uuid4(), score, 1.0, NOW, calibration=calibration) for _ in range(samples)],
        NOW,
    )


def candidate(
    name: str, comp: Competency, *, reliability: Reliability | None = None, workload: int = 0
) -> Candidate:
    return Candidate(
        UUID(int=hash(name) % 2**64), name, {"x": comp}, reliability or Reliability(), workload
    )


def winner(*candidates: Candidate, optimistic: bool) -> str:
    return fitness(list(candidates), optimistic=optimistic)[0].name


def test_low_risk_work_explores_uncertain_agents_and_risky_work_prefers_proven_ones() -> None:
    newcomer = candidate("newcomer", prior(AGENT, "x"))
    mediocre = candidate("veteran", competency(0.6, 40))

    assert winner(newcomer, mediocre, optimistic=True) == "newcomer"
    assert winner(newcomer, mediocre, optimistic=False) == "veteran"


def test_a_proven_strong_agent_wins_regardless_of_risk() -> None:
    strong = candidate("strong", competency(1.0, 30))
    newcomer = candidate("newcomer", prior(AGENT, "x"))

    assert winner(strong, newcomer, optimistic=True) == "strong"
    assert winner(strong, newcomer, optimistic=False) == "strong"


def test_reliability_cost_latency_workload_and_calibration_all_count() -> None:
    same = competency(0.8, 20)
    flaky = candidate("flaky", same, reliability=Reliability(successes=5, failures=15))
    solid = candidate("solid", same, reliability=Reliability(successes=20, failures=0))
    pricey = candidate("pricey", same, reliability=Reliability(10, 0, mean_cost_micro_usd=9_000))
    cheap = candidate("cheap", same, reliability=Reliability(10, 0, mean_cost_micro_usd=1_000))
    busy = candidate("busy", same, workload=5)
    idle = candidate("idle", same, workload=0)
    overconfident = candidate("overconfident", competency(0.8, 20, calibration=0.4))

    assert winner(flaky, solid, optimistic=False) == "solid"
    assert winner(pricey, cheap, optimistic=False) == "cheap"
    assert winner(busy, idle, optimistic=False) == "idle"
    assert (
        winner(
            overconfident,
            candidate("honest", competency(0.8, 20, calibration=0.95)),
            optimistic=False,
        )
        == "honest"
    )


def test_fitness_is_deterministic_and_explained() -> None:
    pair = [candidate("a", competency(0.7, 5)), candidate("b", competency(0.7, 5))]

    first, second = fitness(pair, optimistic=False), fitness(pair, optimistic=False)

    assert first == second
    assert first[0].name == "a"  # ties break by workload then name
    assert "ability" in first[0].describe()
