from dataclasses import replace
from uuid import uuid4

from forge.orchestration.routing import route_task
from tests import factories


def agents(*specs: tuple[str, tuple[str, ...]]):  # type: ignore[no-untyped-def]
    result = []
    for name, capabilities in specs:
        agent, version = factories.agent_and_version(capabilities=capabilities)
        result.append((replace(agent, name=name), replace(version, agent_id=agent.id)))
    return result


def test_capabilities_filter_and_lowest_workload_wins_with_name_tiebreak() -> None:
    pool = agents(("alpha", ("observability",)), ("beta", ("observability", "k8s")), ("gamma", ()))
    task = factories.task(agent_id=None, required_capabilities=("observability",))

    idle = route_task(task, pool, {})
    busy_alpha = route_task(task, pool, {pool[0][0].id: 2})

    assert idle.agent_id == pool[0][0].id  # tie on workload: name order
    assert busy_alpha.agent_id == pool[1][0].id
    assert idle.candidates == (
        "alpha: selected, workload 0",
        "beta: eligible, workload 0",
        "gamma: lacks ['observability']",
    )


def test_explicit_assignment_is_honoured_or_rejected_if_unknown() -> None:
    pool = agents(("alpha", ()))

    assert route_task(factories.task(agent_id=pool[0][0].id), pool, {}).agent_id == pool[0][0].id
    assert route_task(factories.task(agent_id=uuid4()), pool, {}).agent_id is None


def test_no_eligible_agent_is_explained() -> None:
    routing = route_task(
        factories.task(agent_id=None, required_capabilities=("x",)), agents(("a", ())), {}
    )

    assert routing.agent_id is None
    assert routing.explanation == "a: lacks ['x']"
