"""Phase 1 demonstration against the running Docker Compose stack.

Mission -> Agent -> Model -> Tool -> Model -> Result, with the worker container
SIGKILLed while the tool step is in flight and a replacement worker resuming
from the last checkpoint. Run with `make e2e`.
"""

import os
import subprocess
import time
from collections.abc import Iterator
from typing import Any

import httpx2
import pytest

pytestmark = pytest.mark.e2e

API = os.environ.get("FORGE_E2E_API", "http://127.0.0.1:18000")
ENV = {
    **os.environ,
    "FORGE_DEMO_TOOL_DELAY_SECONDS": "4",
    "FORGE_WORKER_LEASE_SECONDS": "3",
}


def compose(*args: str) -> None:
    # Fixed executable and test-controlled arguments only.
    subprocess.run(  # noqa: S603
        ["docker", "compose", *args],  # noqa: S607
        check=True,
        env=ENV,
        capture_output=True,
    )


def wait_for(condition: Any, timeout: float = 60.0) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(0.25)
    raise AssertionError("condition not met in time")


@pytest.fixture
def client() -> Iterator[httpx2.Client]:
    with httpx2.Client(base_url=API, timeout=10) as client:
        yield client


def test_mission_survives_worker_sigkill_and_resumes_without_repeating_work(
    client: httpx2.Client,
) -> None:
    compose("up", "-d", "--wait", "--force-recreate", "--no-deps", "worker")
    agent = client.post(
        "/agents",
        json={
            "name": f"demo-{time.time_ns()}",
            "role": "incident investigator",
            "instructions": "Investigate checkout latency with the tools available.",
            "model": {"provider": "demo", "name": "demo-investigator-1"},
            "tools": ["metrics.read"],
        },
    ).json()
    mission = client.post(
        "/missions",
        json={
            "objective": "Investigate why checkout latency increased after yesterday's deployment.",
            "agent_id": agent["agent_id"],
            "budget_micro_usd": 100_000,
        },
    ).json()
    mission_id = mission["id"]

    def events() -> list[dict[str, Any]]:
        response: list[dict[str, Any]] = client.get(f"/missions/{mission_id}/events").json()
        return response

    def types() -> list[str]:
        return [e["event_type"] for e in events()]

    wait_for(lambda: "tool.called" in types())  # the tool is now sleeping inside the worker
    compose("kill", "-s", "SIGKILL", "worker")
    assert client.get(f"/missions/{mission_id}").json()["status"] == "RUNNING"

    compose("up", "-d", "--no-deps", "worker")  # a replacement worker process
    final = wait_for(
        lambda: (
            (m := client.get(f"/missions/{mission_id}").json())["status"] in {"COMPLETED", "FAILED"}
            and m
        )
    )

    assert final["status"] == "COMPLETED"
    (execution,) = final["executions"]
    assert execution["attempt"] == 2
    assert "14:10=880" in execution["output"]
    history = events()
    kinds = [e["event_type"] for e in history]
    turn_zero = [
        e for e in history if e["event_type"] == "model.called" and e["payload"]["turn"] == 0
    ]
    assert len(turn_zero) == 1, "the committed model turn must not be repeated"
    assert [e["payload"]["attempt"] for e in history if e["event_type"] == "agent.started"] == [
        1,
        2,
    ]
    # metrics.read is READ_ONLY: its interrupted call is safely re-executed with the same key.
    called = [e["payload"] for e in history if e["event_type"] == "tool.called"]
    assert [c["invocation"] for c in called] == [1, 2]
    assert len({c["idempotency_key"] for c in called}) == 1
    assert kinds.count("tool.completed") == 1
    assert kinds.count("mission.completed") == 1
    print(  # noqa: T201 - the demo's narrative output
        f"\nmission {mission_id}: {final['status']} after {execution['attempt']} attempts, "
        f"cost {execution['cost_micro_usd']} micro-USD\n{execution['output']}"
    )
