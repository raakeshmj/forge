"""Phase 9 definition of done, with real process deaths, on both backends (ADR 0011).

A worker process is SIGKILLed in the middle of a tool's side effect; a replacement
process finishes the mission. Effects are counted outside the database, so "no
duplicate side effects" is measured, not inferred.
"""

import asyncio
import os
import signal
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from temporalio.testing import WorkflowEnvironment

from forge.domain.execution import ExecutionStatus, FailureKind
from forge.domain.mission import MissionStatus
from forge.domain.risk import RiskLevel
from forge.domain.tool import idempotency_key
from tests.integration.conftest import UowFactory
from tests.runtime_doubles import submit

pytestmark = [pytest.mark.integration, pytest.mark.anyio]
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="module")
async def temporal() -> AsyncIterator[WorkflowEnvironment]:
    env = await WorkflowEnvironment.start_local(dev_server_log_level="error")
    try:
        yield env
    finally:
        await env.shutdown()


class Scenario:
    def __init__(self, tmp: Path, database_url: str, backend: str, temporal: str) -> None:
        self.effects = tmp / "effects.log"
        self.hang = tmp / "hang"
        self.effects.touch()
        self.env = {
            **os.environ,
            "PYTHONPATH": str(ROOT),
            "FORGE_DATABASE_URL": database_url,
            "FORGE_EXECUTION_BACKEND": backend,
            "FORGE_TEMPORAL_ADDRESS": temporal,
            "FORGE_TEMPORAL_TASK_QUEUE": f"crash-{uuid4().hex[:8]}",
            "CRASH_EFFECTS_FILE": str(self.effects),
            "CRASH_HANG_FILE": str(self.hang),
        }

    async def start(self, idempotency: str) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(  # fixed interpreter and module
            sys.executable,
            "-m",
            "tests.fixtures.crash_worker",
            env={**self.env, "CRASH_TOOL_IDEMPOTENCY": idempotency},
            cwd=ROOT,
        )

    def effect_keys(self) -> list[str]:
        return self.effects.read_text().split()


def _reads(path: Path, content: str) -> bool:
    return path.exists() and path.read_text() == content


async def wait_for_file(path: Path, content: str, attempts: int = 600) -> None:
    for _ in range(attempts):
        if await asyncio.to_thread(_reads, path, content):
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"{path} never became {content!r}")


async def wait_settled(uow: UowFactory, mission_id: UUID, attempts: int = 600) -> MissionStatus:
    for _ in range(attempts):
        async with uow() as tx:
            status = (await tx.missions.get(mission_id)).status
        if status.is_terminal:
            return status
        await asyncio.sleep(0.1)
    raise AssertionError("mission did not settle")


async def kill(process: asyncio.subprocess.Process) -> None:
    process.send_signal(signal.SIGKILL)
    await process.wait()


async def stop(process: asyncio.subprocess.Process) -> None:
    process.send_signal(signal.SIGTERM)
    try:
        await asyncio.wait_for(process.wait(), timeout=20)
    except TimeoutError:
        await kill(process)


@pytest.fixture(params=["postgres", "temporal"])
def scenario(request: pytest.FixtureRequest, tmp_path: Path, database_url: str) -> Scenario:
    address = ""
    if request.param == "temporal":
        env: WorkflowEnvironment = request.getfixturevalue("temporal")
        address = env.client.service_client.config.target_host
    return Scenario(tmp_path, database_url, request.param, address)


@pytest.mark.parametrize(
    ("idempotency", "expected", "distinct_effects"),
    [
        # Outcome unknown and not repeatable: never re-run; a human reconciles (ADR 0002).
        ("NON_IDEMPOTENT", MissionStatus.FAILED, 1),
        # Repeated with the same key: the external system sees one logical effect.
        ("IDEMPOTENT", MissionStatus.COMPLETED, 1),
    ],
)
async def test_a_worker_killed_mid_side_effect_is_replaced_without_duplicating_it(
    scenario: Scenario,
    uow: UowFactory,
    idempotency: str,
    expected: MissionStatus,
    distinct_effects: int,
) -> None:
    mission_id = await submit(uow, tools=("ledger.append",), max_risk=RiskLevel.LOW)
    scenario.hang.write_text("armed")

    first = await scenario.start(idempotency)
    try:
        await wait_for_file(scenario.hang, "hanging")  # the effect happened; no outcome yet
    finally:
        await kill(first)
    scenario.hang.unlink()
    replacement = await scenario.start(idempotency)
    try:
        status = await wait_settled(uow, mission_id)
    finally:
        await stop(replacement)

    assert status is expected
    assert len(set(scenario.effect_keys())) == distinct_effects
    async with uow() as tx:
        (execution,) = await tx.executions.list_for_mission(mission_id)
    key = idempotency_key(execution.id, 0, "c1")
    assert execution.attempt == 2  # recovered by the replacement, not restarted
    assert set(scenario.effect_keys()) == {key}
    if idempotency == "NON_IDEMPOTENT":
        assert scenario.effect_keys() == [key]  # exactly once
        assert execution.failure_kind is FailureKind.TOOL_OUTCOME_UNKNOWN
    else:
        assert execution.status is ExecutionStatus.COMPLETED
