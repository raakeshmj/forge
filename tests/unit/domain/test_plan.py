import pytest

from forge.domain.plan import PlanError, PlannedTask, parse, validate


def task(key: str, *deps: str, tools: tuple[str, ...] | None = None) -> PlannedTask:
    return PlannedTask(key, f"do {key}", depends_on=deps, allowed_tools=tools)


def test_tasks_come_back_in_dependency_order() -> None:
    plan = (task("join", "a", "b"), task("b", "root"), task("a", "root"), task("root"))

    ordered = [t.key for t in validate(plan, None)]

    position = {key: index for index, key in enumerate(ordered)}
    for planned in plan:  # every dependency precedes its dependent
        assert all(position[dep] < position[planned.key] for dep in planned.depends_on)
    assert ordered == [t.key for t in validate(plan, None)]  # deterministic


@pytest.mark.parametrize(
    ("plan", "message"),
    [
        ((), "between 1 and 20"),
        (tuple(task(f"t{i}") for i in range(21)), "between 1 and 20"),
        ((task("a"), task("a")), "unique"),
        ((task("a", "ghost"),), "unknown"),
        ((task("a", "a"),), "itself"),
        ((task("a", "b"), task("b", "c"), task("c", "a")), "cycle"),
        ((PlannedTask("a", "  "),), "objective"),
        ((PlannedTask("a", "x", max_attempts=9),), "max_attempts"),
    ],
)
def test_invalid_plans_are_rejected(plan: tuple[PlannedTask, ...], message: str) -> None:
    with pytest.raises(PlanError, match=message):
        validate(plan, None)


def test_plans_cannot_widen_the_mission_tool_scope() -> None:
    with pytest.raises(PlanError, match="outside the mission scope"):
        validate((task("a", tools=("metrics.read", "deployment.restart")),), ("metrics.read",))
    assert validate((task("a", tools=("metrics.read",)),), ("metrics.read", "logs.read"))


def test_planner_answers_are_parsed_from_prose_and_cannot_choose_agents() -> None:
    answer = (
        "Here is the plan:\n```json\n"
        '{"tasks": [{"key": "metrics", "objective": "read metrics", "agent_id": "x"},'
        ' {"key": "report", "objective": "summarise", "depends_on": ["metrics"]}]}\n```'
    )

    plan = parse(answer)

    assert [(t.key, t.depends_on, t.agent_id) for t in plan] == [
        ("metrics", (), None),
        ("report", ("metrics",), None),
    ]


@pytest.mark.parametrize(
    "answer", ["no json here", "{not json}", '{"steps": []}', '{"tasks": [1]}']
)
def test_malformed_planner_answers_are_rejected(answer: str) -> None:
    with pytest.raises(PlanError):
        parse(answer)
