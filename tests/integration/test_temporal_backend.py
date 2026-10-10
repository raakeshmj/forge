"""The Temporal backend against a real Temporal server (the SDK's local dev server).

Same runtime, same Postgres record, Temporal for dispatch, retries, durable timers,
and external events (ADR 0011). Hard process crashes are in test_process_crashes.py.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment

from forge.domain.execution import ExecutionStatus
from forge.domain.governance import Approval
from forge.domain.mission import MissionStatus
from forge.domain.risk import RiskLevel
from forge.domain.tool import Idempotency
from forge.infrastructure.clock import SystemClock
from forge.infrastructure.temporal import service
from forge.infrastructure.temporal.dispatch import DispatchSettings, dispatch_once
from forge.infrastructure.temporal.wakeup import TemporalWakeup
from forge.infrastructure.temporal.workflows import WORKFLOW, TaskRun, workflow_id
from forge.runtime.agent_loop import Runtime
from forge.security import approvals
from tests.integration.conftest import UowFactory
from tests.runtime_doubles import RecordingTool, ScriptedModel, answer, runtime, submit, tool_call

pytestmark = [pytest.mark.integration, pytest.mark.anyio]
CLOCK = SystemClock()


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    return "asyncio"  # one event loop for the module, so one Temporal server serves it


@pytest.fixture(scope="module")
async def temporal() -> AsyncIterator[WorkflowEnvironment]:
    env = await WorkflowEnvironment.start_local(dev_server_log_level="error")
    try:
        yield env
    finally:
        await env.shutdown()


def settings(**overrides: object) -> DispatchSettings:
    base = DispatchSettings(
        task_queue=f"forge-test-{uuid4().hex[:8]}",
        poll_interval_seconds=0.05,
        approval_poll_seconds=300.0,  # so only a signal or a timer can resume within a test
        heartbeat_seconds=2.0,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


class Running:
    """A Temporal-backed worker running in the background of a test."""

    def __init__(self, client: Client, rt: Runtime, dispatch: DispatchSettings) -> None:
        self.stop = asyncio.Event()
        self.task = asyncio.create_task(
            service.run_until(client, rt, dispatch, self.stop, concurrency=4)
        )

    async def close(self) -> None:
        self.stop.set()
        await self.task


async def eventually(check: Callable[[], Awaitable[bool]], attempts: int = 300) -> None:
    for _ in range(attempts):
        if await check():
            return
        await asyncio.sleep(0.1)
    raise AssertionError("condition not reached")


def mission_is(
    uow: UowFactory, mission_id: UUID, status: MissionStatus
) -> Callable[[], Awaitable[bool]]:
    async def check() -> bool:
        async with uow() as tx:
            return (await tx.missions.get(mission_id)).status is status

    return check


async def approval_of(uow: UowFactory, mission_id: UUID) -> Approval:
    async with uow() as tx:
        (approval,) = [a for a in await tx.approvals.list_approvals() if a.mission_id == mission_id]
    return approval


async def only_task(uow: UowFactory, mission_id: UUID) -> UUID:
    async with uow() as tx:
        (task,) = await tx.tasks.list_for_mission(mission_id)
    return task.id


async def test_a_mission_completes_on_temporal_and_history_holds_no_payloads(
    uow: UowFactory, temporal: WorkflowEnvironment
) -> None:
    objective = "Why is checkout slow?"  # what `submit` asks
    model = ScriptedModel(tool_call(service="checkout"), answer("p95 regressed"))
    tool = RecordingTool()
    mission_id = await submit(uow)
    worker = Running(temporal.client, runtime(uow, model, tool), settings())
    try:
        await eventually(mission_is(uow, mission_id, MissionStatus.COMPLETED))
    finally:
        await worker.close()

    task_id = await only_task(uow, mission_id)
    handle = temporal.client.get_workflow_handle(workflow_id(str(task_id), 1))
    assert await handle.result() == "COMPLETED"
    history = (await handle.fetch_history()).to_json()
    for payload in (objective, "p95 regressed", "checkout", "checking"):
        assert payload not in history  # prompts, outputs, arguments stay out of Temporal
    assert len(tool.invocations) == 1


async def test_an_approval_signal_resumes_a_parked_task_at_once(
    uow: UowFactory, temporal: WorkflowEnvironment
) -> None:
    restart = RecordingTool(
        name="deployment.restart", risk=RiskLevel.HIGH, idempotency=Idempotency.NON_IDEMPOTENT
    )
    model = ScriptedModel(tool_call("deployment.restart"), answer("restarted"))
    mission_id = await submit(uow, tools=("deployment.restart",), max_risk=RiskLevel.HIGH)
    worker = Running(temporal.client, runtime(uow, model, restart), settings())
    try:
        await eventually(mission_is(uow, mission_id, MissionStatus.WAITING_APPROVAL))
        approval = await approval_of(uow, mission_id)
        await approvals.decide(uow, CLOCK, approval.id, approve=True, operator="a", reason="ok")
        async with uow() as tx:
            execution = await tx.executions.get(approval.execution_id)
        await TemporalWakeup(temporal.client.service_client.config.target_host, "default").wake(
            [execution]
        )
        await eventually(mission_is(uow, mission_id, MissionStatus.COMPLETED), attempts=100)
    finally:
        await worker.close()

    assert len(restart.invocations) == 1


async def test_the_durable_timer_resumes_a_parked_task_at_expiry(
    uow: UowFactory, temporal: WorkflowEnvironment
) -> None:
    restart = RecordingTool(name="deployment.restart", risk=RiskLevel.HIGH)
    model = ScriptedModel(tool_call("deployment.restart"), answer("nobody approved"))
    mission_id = await submit(uow, tools=("deployment.restart",), max_risk=RiskLevel.HIGH)
    rt = replace(runtime(uow, model, restart), approval_ttl_seconds=2.0)
    worker = Running(temporal.client, rt, settings())
    try:  # no signal is ever sent: only the timer can resume it
        await eventually(mission_is(uow, mission_id, MissionStatus.COMPLETED), attempts=150)
    finally:
        await worker.close()

    assert restart.invocations == []
    approval = await approval_of(uow, mission_id)
    assert approval.status.value == "EXPIRED"


async def test_repeated_and_concurrent_dispatch_starts_one_workflow(
    uow: UowFactory, temporal: WorkflowEnvironment
) -> None:
    await submit(uow)
    dispatch = settings()

    counts = await asyncio.gather(
        *(dispatch_once(temporal.client, uow, dispatch) for _ in range(5))
    )

    assert sum(counts) == 1


async def test_a_worker_shutdown_mid_step_hands_the_task_to_another_worker(
    uow: UowFactory, temporal: WorkflowEnvironment
) -> None:
    entered = asyncio.Event()

    async def hang_until_cancelled() -> None:
        entered.set()
        await asyncio.sleep(3600)

    model = ScriptedModel(tool_call(), answer(), answer())
    first_tool = RecordingTool(on_invoke=hang_until_cancelled)
    mission_id = await submit(uow)
    dispatch = settings()
    first = Running(temporal.client, runtime(uow, model, first_tool, worker_id="a"), dispatch)
    await asyncio.wait_for(entered.wait(), timeout=30)
    await first.close()  # graceful: the activity is cancelled and its lease released

    second_tool = RecordingTool()
    second = Running(temporal.client, runtime(uow, model, second_tool, worker_id="b"), dispatch)
    try:
        await eventually(mission_is(uow, mission_id, MissionStatus.COMPLETED))
    finally:
        await second.close()

    assert len(first_tool.invocations) == 1
    assert len(second_tool.invocations) == 1  # the READ_ONLY read was repeated, safely
    async with uow() as tx:
        (execution,) = await tx.executions.list_for_mission(mission_id)
    assert execution.status is ExecutionStatus.COMPLETED
    assert execution.attempt == 2


async def test_long_waits_continue_as_new_and_still_resume(
    uow: UowFactory, temporal: WorkflowEnvironment
) -> None:
    restart = RecordingTool(name="deployment.restart", risk=RiskLevel.HIGH)
    model = ScriptedModel(tool_call("deployment.restart"), answer("done"))
    mission_id = await submit(uow, tools=("deployment.restart",), max_risk=RiskLevel.HIGH)
    task_id = await only_task(uow, mission_id)
    dispatch = settings(poll_interval_seconds=3600.0)  # this test starts the workflow itself
    worker = Running(temporal.client, runtime(uow, model, restart), dispatch)
    try:
        handle = await temporal.client.start_workflow(
            WORKFLOW,
            TaskRun(
                str(task_id), 1, poll_seconds=1.0, heartbeat_seconds=2.0, waits_before_continue=2
            ),
            id=workflow_id(str(task_id), 1),
            task_queue=dispatch.task_queue,
        )
        await eventually(mission_is(uow, mission_id, MissionStatus.WAITING_APPROVAL))
        await asyncio.sleep(4)  # several polls: at least one continue-as-new
        approval = await approval_of(uow, mission_id)
        await approvals.decide(uow, CLOCK, approval.id, approve=True, operator="a", reason="ok")
        assert await handle.result() == "COMPLETED"
    finally:
        await worker.close()

    description = await handle.describe()
    assert description.run_id != handle.first_execution_run_id  # history was rolled over
    assert len(restart.invocations) == 1
