"""Mission plans: validated task graphs (ADR 0006).

A plan may come from the operator or from a planner agent. Either way it is
checked here before any task exists: unique keys, known dependencies, no
cycles, bounded size, and tools within the mission's scope.
"""

import json
from dataclasses import dataclass
from uuid import UUID

from forge.domain.common import DomainError

MAX_TASKS = 20


class PlanError(DomainError):
    """The plan is not a valid task graph. Never retryable as-is."""


@dataclass(frozen=True, slots=True)
class PlannedTask:
    key: str
    objective: str
    depends_on: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    agent_id: UUID | None = None
    allowed_tools: tuple[str, ...] | None = None  # None: the mission's tool scope
    max_attempts: int = 1
    agent_version_id: UUID | None = None  # pin an exact agent version (offline validation)


def validate(
    tasks: tuple[PlannedTask, ...], tool_scope: tuple[str, ...] | None
) -> tuple[PlannedTask, ...]:
    """Return the tasks in dependency order, or raise PlanError."""
    if not 1 <= len(tasks) <= MAX_TASKS:
        raise PlanError(f"a plan needs between 1 and {MAX_TASKS} tasks, got {len(tasks)}")
    keys = [task.key for task in tasks]
    if len(set(keys)) != len(keys):
        raise PlanError("task keys must be unique")
    known = set(keys)
    for task in tasks:
        if not task.key or not task.objective.strip():
            raise PlanError("every task needs a key and an objective")
        unknown = set(task.depends_on) - known
        if unknown:
            raise PlanError(f"task {task.key!r} depends on unknown tasks {sorted(unknown)}")
        if task.key in task.depends_on:
            raise PlanError(f"task {task.key!r} depends on itself")
        if not 1 <= task.max_attempts <= 5:
            raise PlanError(f"task {task.key!r}: max_attempts must be between 1 and 5")
        if tool_scope is not None and task.allowed_tools is not None:
            beyond = set(task.allowed_tools) - set(tool_scope)
            if beyond:
                raise PlanError(
                    f"task {task.key!r} asks for tools outside the mission scope: {sorted(beyond)}"
                )
    return _topological(tasks)


def parse(text: str) -> tuple[PlannedTask, ...]:
    """Parse a planner agent's final answer. The JSON may be wrapped in prose or a code fence."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise PlanError("the planner's answer contains no JSON object")
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise PlanError(f"the planner's answer is not valid JSON: {exc.msg}") from exc
    raw_tasks = payload.get("tasks") if isinstance(payload, dict) else None
    if not isinstance(raw_tasks, list):
        raise PlanError("the plan must be an object with a 'tasks' list")
    return tuple(_planned(item) for item in raw_tasks)


def _planned(item: object) -> PlannedTask:
    if not isinstance(item, dict):
        raise PlanError("each planned task must be an object")
    try:
        tools = item.get("allowed_tools")
        return PlannedTask(
            key=str(item["key"]),
            objective=str(item["objective"]),
            depends_on=tuple(str(d) for d in item.get("depends_on", [])),
            required_capabilities=tuple(str(c) for c in item.get("required_capabilities", [])),
            allowed_tools=None if tools is None else tuple(str(t) for t in tools),
            max_attempts=int(item.get("max_attempts", 1)),
        )  # a planner cannot pick agent ids: routing decides (ADR 0006)
    except (KeyError, TypeError, ValueError) as exc:
        raise PlanError(f"malformed planned task: {exc}") from exc


def _topological(tasks: tuple[PlannedTask, ...]) -> tuple[PlannedTask, ...]:
    by_key = {task.key: task for task in tasks}
    ordered: list[PlannedTask] = []
    state: dict[str, str] = {}

    def visit(key: str, path: tuple[str, ...]) -> None:
        if state.get(key) == "done":
            return
        if state.get(key) == "visiting":
            raise PlanError(f"the plan has a dependency cycle: {' -> '.join((*path, key))}")
        state[key] = "visiting"
        for dependency in by_key[key].depends_on:
            visit(dependency, (*path, key))
        state[key] = "done"
        ordered.append(by_key[key])

    for task in tasks:  # original order breaks ties, so ordering is deterministic
        visit(task.key, ())
    return tuple(ordered)
