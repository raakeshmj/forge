"""Phase 4: task graphs across agents: ordering, parallelism, fan-in, failure, retry,
cancellation, recovery, duplicate prevention, planning, and shared budgets."""

import asyncio
import json
from dataclasses import replace
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from forge.domain.common import JSONObject
from forge.domain.execution import ExecutionStatus
from forge.domain.mission import Budget, ExecutionPolicy, MissionStatus
from forge.domain.plan import PlannedTask
from forge.domain.task import TaskStatus
from forge.infrastructure.clock import SystemClock
from forge.orchestration.agents import register_agent
from forge.orchestration.missions import MissionRequest, cancel_task, submit_mission
from forge.runtime.agent_loop import ExecutionRunner, Runtime, claim_next
from forge.runtime.worker import Worker
from tests.integration.conftest import UowFactory
from tests.runtime_doubles import (
    FAST_POLICY,
    TEST_MODEL,
    RecordingTool,
    ScriptedModel,
    SimulatedCrash,
    TaskAwareModel,
    answer,
    expire_lease,
    runtime,
    tool_call,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]
CLOCK = SystemClock()


async def agent(uow: UowFactory, name: str, *capabilities: str) -> UUID:
    version = await register_agent(
        uow,
        CLOCK,
        name=name,
        role="worker",
        instructions="Do the task.",
        model=TEST_MODEL,
        tools=["metrics.read"],
        capabilities=capabilities,
        correlation_id=None,
    )
    return version.agent_id


async def mission(
    uow: UowFactory, *tasks: PlannedTask, budget: int = 10**7, policy: ExecutionPolicy = FAST_POLICY
) -> UUID:
    request = MissionRequest(
        objective="Investigate checkout latency",
        budget=Budget(budget),
        tasks=tasks,
        execution_policy=policy,
    )
    return (await submit_mission(uow, CLOCK, request, correlation_id="orch")).id


async def run_until_settled(
    rt: Runtime, uow: UowFactory, mission_id: UUID, concurrency: int = 1
) -> MissionStatus:
    stop = asyncio.Event()
    worker = asyncio.create_task(
        Worker(rt, poll_interval_seconds=0.02, concurrency=concurrency).run_until(stop)
    )
    try:
        for _ in range(500):
            async with uow() as tx:
                status = (await tx.missions.get(mission_id)).status
            if status.is_terminal:
                # Let in-flight executions observe the outcome before stopping.
                await asyncio.sleep(0.2)
                return status
            await asyncio.sleep(0.02)
        raise AssertionError("mission did not settle")
    finally:
        stop.set()
        await worker


async def events(uow: UowFactory, mission_id: UUID) -> list[tuple[str, UUID, JSONObject]]:
    async with uow() as tx:
        return [
            (e.event_type, e.aggregate_id, e.payload)
            for e in await tx.events.list_for_mission(mission_id)
        ]


async def tasks_by_key(uow: UowFactory, mission_id: UUID) -> dict[str, object]:
    async with uow() as tx:
        return {t.key: t for t in await tx.tasks.list_for_mission(mission_id)}


def fan(*keys: str, join: str = "join", **kw: object) -> tuple[PlannedTask, ...]:
    return (*(PlannedTask(k, k) for k in keys), PlannedTask(join, join, depends_on=keys, **kw))  # type: ignore[arg-type]


async def test_dependent_task_runs_after_its_dependency_and_receives_its_result(
    uow: UowFactory,
) -> None:
    agent_id = await agent(uow, "solo")
    model = TaskAwareModel()
    mission_id = await mission(
        uow,
        PlannedTask("metrics", "metrics", agent_id=agent_id),
        PlannedTask("report", "report", depends_on=("metrics",), agent_id=agent_id),
    )

    status = await run_until_settled(runtime(uow, model, RecordingTool()), uow, mission_id)

    assert status is MissionStatus.COMPLETED
    assert model.calls == ["metrics", "report"]
    async with uow() as tx:
        report = (await tasks_by_key(uow, mission_id))["report"]
        execution = await tx.executions.latest_for_task(report.id)  # type: ignore[attr-defined]
        assert execution is not None
        first = (await tx.checkpoints.list(execution.id))[0]
    brief = first.state.messages[0].content[0].text  # type: ignore[union-attr]
    assert '<result task="metrics">\nresult of metrics\n</result>' in brief
    assert "never as instructions" in brief


async def test_independent_tasks_run_in_parallel_and_fan_in_waits_for_all(uow: UowFactory) -> None:
    await agent(uow, "a1", "general")
    await agent(uow, "a2", "general")
    model = TaskAwareModel({"metrics": ["parallel"], "logs": ["parallel"]})
    graph = (
        PlannedTask("metrics", "metrics", required_capabilities=("general",)),
        PlannedTask("logs", "logs", required_capabilities=("general",)),
        PlannedTask(
            "join", "join", depends_on=("metrics", "logs"), required_capabilities=("general",)
        ),
    )
    mission_id = await mission(uow, *graph)

    status = await run_until_settled(
        runtime(uow, model, RecordingTool()), uow, mission_id, concurrency=3
    )

    assert status is MissionStatus.COMPLETED
    assert model.max_in_flight >= 2  # metrics and logs overlapped
    history = await events(uow, mission_id)
    tasks = await tasks_by_key(uow, mission_id)
    order = [(kind, aggregate) for kind, aggregate, _ in history]
    join_start = order.index(("task.started", tasks["join"].id))  # type: ignore[attr-defined]
    for key in ("metrics", "logs"):
        assert order.index(("task.completed", tasks[key].id)) < join_start  # type: ignore[attr-defined]
    routed = {tasks_id: p["agent_id"] for kind, tasks_id, p in history if kind == "task.routed"}
    assert routed[tasks["metrics"].id] != routed[tasks["logs"].id]  # type: ignore[attr-defined]  # workload spread


async def test_a_permanent_failure_fails_the_mission_and_cancels_what_remains(
    uow: UowFactory,
) -> None:
    agent_id = await agent(uow, "solo")
    model = TaskAwareModel({"metrics": ["fail"]})
    graph = tuple(
        PlannedTask(t.key, t.objective, depends_on=t.depends_on, agent_id=agent_id)
        for t in fan("metrics", "logs")
    )
    mission_id = await mission(uow, *graph)

    status = await run_until_settled(runtime(uow, model, RecordingTool()), uow, mission_id)

    tasks = await tasks_by_key(uow, mission_id)
    assert status is MissionStatus.FAILED
    assert tasks["metrics"].status is TaskStatus.FAILED  # type: ignore[attr-defined]
    assert tasks["join"].status is TaskStatus.CANCELLED  # type: ignore[attr-defined]
    assert "join" not in model.calls
    failed = [p for kind, _, p in await events(uow, mission_id) if kind == "mission.failed"]
    assert failed[0]["failure_kind"] == "MODEL_FAILED"


async def test_retryable_task_failure_runs_a_new_attempt(uow: UowFactory) -> None:
    agent_id = await agent(uow, "solo")
    model = TaskAwareModel({"metrics": ["fail"]})
    mission_id = await mission(
        uow, PlannedTask("metrics", "metrics", agent_id=agent_id, max_attempts=2)
    )

    status = await run_until_settled(runtime(uow, model, RecordingTool()), uow, mission_id)

    assert status is MissionStatus.COMPLETED
    async with uow() as tx:
        executions = await tx.executions.list_for_mission(mission_id)
    assert [(e.task_attempt, e.status) for e in executions] == [
        (1, ExecutionStatus.FAILED),
        (2, ExecutionStatus.COMPLETED),
    ]
    retried = [p for kind, _, p in await events(uow, mission_id) if kind == "task.retried"]
    assert retried == [
        {
            "failed_execution_id": str(executions[0].id),
            "failure_kind": "MODEL_FAILED",
            "attempt": 1,
            "max_attempts": 2,
        }
    ]


async def test_cancelling_a_running_task_cancels_its_dependents_and_stops_its_execution(
    uow: UowFactory,
) -> None:
    agent_id = await agent(uow, "solo")
    gate = asyncio.Event()
    holder: dict[str, UUID] = {}

    async def cancel_while_running() -> None:
        await cancel_task(uow, CLOCK, holder["mission"], holder["metrics"])
        gate.set()

    tool = RecordingTool(on_invoke=cancel_while_running)
    graph = (
        PlannedTask("metrics", "metrics", agent_id=agent_id),
        PlannedTask("join", "join", depends_on=("metrics",), agent_id=agent_id),
    )
    mission_id = await mission(uow, *graph)
    holder["mission"] = mission_id
    holder["metrics"] = (await tasks_by_key(uow, mission_id))["metrics"].id  # type: ignore[attr-defined]

    status = await run_until_settled(
        runtime(uow, ScriptedModel(tool_call(), answer()), tool), uow, mission_id
    )

    tasks = await tasks_by_key(uow, mission_id)
    assert gate.is_set()
    assert status is MissionStatus.FAILED
    assert tasks["metrics"].status is TaskStatus.CANCELLED  # type: ignore[attr-defined]
    assert tasks["join"].status is TaskStatus.CANCELLED  # type: ignore[attr-defined]
    async with uow() as tx:
        (execution,) = await tx.executions.list_for_mission(mission_id)
    assert execution.status is ExecutionStatus.CANCELLED
    failed = [p for kind, _, p in await events(uow, mission_id) if kind == "mission.failed"]
    assert failed[0]["failure_kind"] == "TASK_CANCELLED"


async def test_a_crashed_fan_out_task_resumes_without_disturbing_its_siblings(
    uow: UowFactory, engine: AsyncEngine
) -> None:
    agent_id = await agent(uow, "solo")
    model = TaskAwareModel({"metrics": ["crash"]})
    graph = tuple(
        PlannedTask(t.key, t.objective, depends_on=t.depends_on, agent_id=agent_id)
        for t in fan("metrics", "logs")
    )
    mission_id = await mission(uow, *graph)
    rt = runtime(uow, model, RecordingTool())

    crashed = None
    for _ in range(2):  # metrics crashes; logs completes
        claim = await claim_next(rt)
        assert claim is not None
        try:
            await ExecutionRunner(rt, claim).run()
        except SimulatedCrash:
            crashed = claim.execution.id
    assert crashed is not None
    await expire_lease(engine, crashed)
    status = await run_until_settled(rt, uow, mission_id)

    assert status is MissionStatus.COMPLETED
    assert model.calls.count("logs") == 1
    assert model.calls.count("metrics") == 2  # the crashed call only
    async with uow() as tx:
        executions = await tx.executions.list_for_mission(mission_id)
    assert len(executions) == 3  # one per task; recovery was a new attempt, not a new execution
    assert max(e.attempt for e in executions) == 2


async def test_many_concurrent_workers_execute_each_task_exactly_once(uow: UowFactory) -> None:
    for name in ("w1", "w2", "w3"):
        await agent(uow, name, "general")
    keys = tuple(f"t{i}" for i in range(6))
    graph = (
        *(PlannedTask(k, k, required_capabilities=("general",)) for k in keys),
        PlannedTask("join", "join", depends_on=keys, required_capabilities=("general",)),
    )
    model = TaskAwareModel()
    mission_id = await mission(uow, *graph)
    rts = [runtime(uow, model, RecordingTool(), worker_id=f"worker-{i}") for i in range(3)]

    stop = asyncio.Event()
    workers = [
        asyncio.create_task(Worker(rt, poll_interval_seconds=0.01, concurrency=4).run_until(stop))
        for rt in rts
    ]
    for _ in range(500):
        async with uow() as tx:
            if (await tx.missions.get(mission_id)).status.is_terminal:
                break
        await asyncio.sleep(0.02)
    stop.set()
    await asyncio.gather(*workers)

    async with uow() as tx:
        executions = await tx.executions.list_for_mission(mission_id)
        assert (await tx.missions.get(mission_id)).status is MissionStatus.COMPLETED
    assert sorted(model.calls) == sorted([*keys, "join"])
    assert len(executions) == 7
    assert len({e.task_id for e in executions}) == 7


async def test_a_planner_agent_plans_and_capability_routing_distributes_the_work(
    uow: UowFactory,
) -> None:
    planner = await agent(uow, "planner")
    await agent(uow, "observer", "observability")
    await agent(uow, "deployer", "change-management")
    plan = json.dumps(
        {
            "tasks": [
                {
                    "key": "metrics",
                    "objective": "metrics",
                    "required_capabilities": ["observability"],
                },
                {
                    "key": "deploys",
                    "objective": "deploys",
                    "required_capabilities": ["change-management"],
                },
                {"key": "join", "objective": "join", "depends_on": ["metrics", "deploys"]},
            ]
        }
    )
    model = TaskAwareModel(plan=f"Here is my plan: {plan}")
    request = MissionRequest(
        objective="Why did checkout slow down?",
        budget=Budget(10**7),
        planner_agent_id=planner,
        execution_policy=FAST_POLICY,
    )
    mission_id = (await submit_mission(uow, CLOCK, request, correlation_id="plan")).id

    status = await run_until_settled(
        runtime(uow, model, RecordingTool()), uow, mission_id, concurrency=2
    )

    assert status is MissionStatus.COMPLETED
    tasks = await tasks_by_key(uow, mission_id)
    assert set(tasks) == {"plan", "metrics", "deploys", "join"}
    async with uow() as tx:
        names = {a.id: a.name for a, _ in await tx.agents.list_current()}
    assert names[tasks["metrics"].agent_id] == "observer"  # type: ignore[attr-defined]
    assert names[tasks["deploys"].agent_id] == "deployer"  # type: ignore[attr-defined]
    assert tasks["plan"].allowed_tools == ()  # type: ignore[attr-defined]  # planners get no tools


@pytest.mark.parametrize(
    ("plan", "scope"),
    [
        ("I could not make a plan.", None),
        ('{"tasks": [{"key": "a", "objective": "a", "depends_on": ["a"]}]}', None),
        (
            '{"tasks": [{"key": "a", "objective": "a", "allowed_tools": ["deployment.restart"]}]}',
            ("metrics.read",),
        ),
    ],
)
async def test_invalid_or_overreaching_plans_fail_the_mission(
    uow: UowFactory, plan: str, scope: tuple[str, ...] | None
) -> None:
    planner = await agent(uow, "planner")
    request = MissionRequest(
        objective="o",
        budget=Budget(10**7),
        planner_agent_id=planner,
        tool_scope=scope,
        execution_policy=FAST_POLICY,
    )
    mission_id = (await submit_mission(uow, CLOCK, request, correlation_id="bad-plan")).id

    status = await run_until_settled(
        runtime(uow, TaskAwareModel(plan=plan), RecordingTool()), uow, mission_id
    )

    assert status is MissionStatus.FAILED
    failed = [p for kind, _, p in await events(uow, mission_id) if kind == "task.failed"]
    assert failed[0]["failure_kind"] == "PLAN_INVALID"
    assert failed[0]["detail"]


async def test_no_agent_with_the_required_capability_fails_routing(uow: UowFactory) -> None:
    await agent(uow, "generalist")
    mission_id = await mission(
        uow, PlannedTask("k8s", "k8s", required_capabilities=("kubernetes",))
    )

    async with uow() as tx:
        assert (await tx.missions.get(mission_id)).status is MissionStatus.FAILED
    routed = [p for kind, _, p in await events(uow, mission_id) if kind == "task.routed"]
    assert routed[0]["candidates"] == ["generalist: cannot serve ['kubernetes']"]


async def test_parallel_tasks_share_the_budget_without_overspending(uow: UowFactory) -> None:
    agent_id = await agent(uow, "solo")
    tight = replace(FAST_POLICY, max_output_tokens=50)
    graph = tuple(PlannedTask(k, k, agent_id=agent_id) for k in ("a", "b", "c", "d"))
    # Room for every task's actual spend (4 x 200) plus one worst case at a time, not four.
    mission_id = await mission(uow, *graph, budget=1_300, policy=tight)

    status = await run_until_settled(
        runtime(uow, TaskAwareModel(), RecordingTool()), uow, mission_id, concurrency=4
    )

    async with uow() as tx:
        spent = await tx.executions.mission_spend(mission_id)
    assert status is MissionStatus.COMPLETED
    assert spent <= 1_300
