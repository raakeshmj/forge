from dataclasses import replace
from uuid import uuid4

from forge.domain.capability import Capability, CapabilityGraph, Observation, estimate
from forge.orchestration.routing import RoutingEvidence, route_task
from tests import factories
from tests.factories import NOW

GRAPH = CapabilityGraph({"observability": Capability("observability", parent="incident-response")})


def agents(*specs: tuple[str, tuple[str, ...]]):  # type: ignore[no-untyped-def]
    result = []
    for name, capabilities in specs:
        agent, version = factories.agent_and_version(capabilities=capabilities)
        result.append((replace(agent, name=name), replace(version, agent_id=agent.id)))
    return result


def evidence(**overrides: object) -> RoutingEvidence:
    base = RoutingEvidence(GRAPH, {}, {}, {}, optimistic=False)
    return replace(base, **overrides)  # type: ignore[arg-type]


def test_graph_filters_and_fitness_orders_with_deterministic_tiebreak() -> None:
    pool = agents(("alpha", ("observability",)), ("beta", ("observability", "k8s")), ("gamma", ()))
    task = factories.task(agent_id=None, required_capabilities=("incident-response",))

    routing = route_task(task, pool, evidence())

    assert routing.agent_id == pool[0][0].id  # equal evidence: name order
    assert routing.candidates[0].startswith("selected alpha: fitness")
    assert routing.candidates[-1] == "gamma: cannot serve ['incident-response']"


def test_measured_competency_beats_name_order() -> None:
    pool = agents(("alpha", ("observability",)), ("beta", ("observability",)))
    proven = estimate(
        pool[1][0].id,
        "observability",
        [Observation(uuid4(), 1.0, 1.0, NOW) for _ in range(10)],
        NOW,
    )
    task = factories.task(agent_id=None, required_capabilities=("observability",))

    routing = route_task(
        task, pool, evidence(competencies={(pool[1][0].id, "observability"): proven})
    )

    assert routing.agent_id == pool[1][0].id


def test_explicit_assignment_is_honoured_or_rejected_if_unknown() -> None:
    pool = agents(("alpha", ()))

    assert (
        route_task(factories.task(agent_id=pool[0][0].id), pool, evidence()).agent_id
        == pool[0][0].id
    )
    assert route_task(factories.task(agent_id=uuid4()), pool, evidence()).agent_id is None


def test_no_eligible_agent_is_explained() -> None:
    task = factories.task(agent_id=None, required_capabilities=("x",))

    routing = route_task(task, agents(("a", ())), evidence())

    assert routing.agent_id is None
    assert routing.explanation == "a: cannot serve ['x']"
