"""Phase 9 chaos against the running Docker Compose stack, on whichever backend it runs.

Three workers drive a stream of governed missions (investigate, then a HIGH-risk rollback an
operator approves) while the test SIGKILLs random workers, freezes one past its lease
(a stale worker), and restarts PostgreSQL. Every mission must settle, and no rollback
may ever run twice. Run with `make e2e` (or `make e2e-temporal`).
"""

import os
import random
import subprocess
import time
from collections.abc import Iterator
from typing import Any

import httpx2
import pytest

pytestmark = pytest.mark.e2e

API = os.environ.get("FORGE_E2E_API", "http://127.0.0.1:18000")
TOKEN = "chaos-" + "c" * 40
LEASE = "3"
ENV = {
    **os.environ,
    "FORGE_OPERATOR_TOKENS": f"chaos-operator:{TOKEN}",
    "FORGE_WORKER_LEASE_SECONDS": LEASE,
    "FORGE_DEMO_TOOL_DELAY_SECONDS": "1.5",  # long enough for kills to land mid-step
}
PROFILE = (
    ["--profile", "temporal"] if os.environ.get("FORGE_EXECUTION_BACKEND") == "temporal" else []
)
INITIAL_MISSIONS = 4
WORKERS = 3


def run(*args: str) -> str:
    # Fixed executables and test-controlled arguments only.
    result = subprocess.run(  # noqa: S603
        list(args), check=True, env=ENV, capture_output=True, text=True
    )
    return result.stdout


def compose(*args: str) -> str:
    return run("docker", "compose", *PROFILE, *args)


def workers() -> list[str]:
    return compose("ps", "-q", "worker").split()


@pytest.fixture
def client() -> Iterator[httpx2.Client]:
    compose("up", "-d", "--wait", "--force-recreate", "--no-deps", "api")
    compose(
        "up",
        "-d",
        "--wait",
        "--force-recreate",
        "--no-deps",
        "--scale",
        f"worker={WORKERS}",
        "worker",
    )
    with httpx2.Client(base_url=API, timeout=10) as client:
        yield client
    compose("up", "-d", "--wait", "--no-deps", "--scale", "worker=1", "worker")


def tolerant(call: Any) -> Any:
    """The API itself rides out the PostgreSQL restart; the test retries through it."""
    for _ in range(60):
        try:
            response = call()
            if response.status_code < 500:
                return response
        except httpx2.TransportError:
            pass
        time.sleep(0.5)
    raise AssertionError("API did not recover")


def test_missions_settle_without_duplicate_side_effects_under_chaos(
    client: httpx2.Client,
) -> None:
    rng = random.Random(9)  # noqa: S311 - reproducible chaos, not cryptography
    agent = client.post(
        "/agents",
        json={
            "name": f"chaos-{time.time_ns()}",
            "role": "incident responder",
            "instructions": "Investigate checkout latency and remediate if the deploy caused it.",
            "model": {"provider": "demo", "name": "demo-investigator-1"},
            "tools": ["metrics.read", "deployment.rollback"],
        },
    ).json()

    def submit() -> str:
        response = tolerant(
            lambda: client.post(
                "/missions",
                json={
                    "objective": "Investigate why checkout latency increased after the deployment.",
                    "agent_id": agent["agent_id"],
                    "budget_micro_usd": 100_000,
                    "max_risk": "HIGH",
                },
            )
        )
        mission_id: str = response.json()["id"]
        return mission_id

    mission_ids = [submit() for _ in range(INITIAL_MISSIONS)]
    operator = {"Authorization": f"Bearer {TOKEN}"}

    def approve_pending() -> None:
        pending = tolerant(lambda: client.get("/approvals", params={"status": "PENDING"})).json()
        for approval in pending:
            if approval["mission_id"] in mission_ids:
                tolerant(
                    lambda a=approval: client.post(
                        f"/approvals/{a['id']}/decision",
                        json={"decision": "approve", "reason": "chaos drill"},
                        headers=operator,
                    )
                )

    def settled() -> list[dict[str, Any]]:
        missions = [tolerant(lambda m=m: client.get(f"/missions/{m}")).json() for m in mission_ids]
        terminal = {"COMPLETED", "FAILED", "CANCELLED"}
        return missions if all(m["status"] in terminal for m in missions) else []

    # Every action runs, each with fresh work in flight.
    for action in ["kill", "kill", "stale", "kill", "postgres", "kill", "stale", "kill"]:
        mission_ids.append(submit())
        approve_pending()
        victim = rng.choice(workers())
        if action == "kill":
            run("docker", "kill", "-s", "SIGKILL", victim)
        elif action == "stale":
            run("docker", "pause", victim)
            time.sleep(float(LEASE) * 2)  # its leases expire and are taken over
            run("docker", "unpause", victim)
        else:
            compose("restart", "postgres")
        compose("up", "-d", "--no-deps", "--scale", f"worker={WORKERS}", "worker")
        time.sleep(2)

    deadline = time.monotonic() + 180
    while not (missions := settled()):
        assert time.monotonic() < deadline, "missions did not settle after the chaos"
        approve_pending()
        time.sleep(1)

    for mission in missions:
        events = tolerant(lambda m=mission: client.get(f"/missions/{m['id']}/events")).json()
        rollbacks = [
            e["payload"]
            for e in events
            if e["event_type"] == "tool.called" and e["payload"]["tool"] == "deployment.rollback"
        ]
        assert len(rollbacks) <= 1, mission["id"]  # never twice
        assert all(r["invocation"] == 1 for r in rollbacks)
        (execution,) = mission["executions"]
        if mission["status"] == "COMPLETED":
            assert len(rollbacks) == 1
            assert "Remediation: Rolled back checkout" in execution["output"]
        else:  # the only acceptable failure: killed mid-rollback, outcome unknown
            assert execution["failure_kind"] == "TOOL_OUTCOME_UNKNOWN", execution
    completed = sum(m["status"] == "COMPLETED" for m in missions)
    assert completed > 0
    print(  # noqa: T201 - the drill's narrative output
        f"chaos: {completed}/{len(missions)} completed, attempts:",
        sorted(m["executions"][0]["attempt"] for m in missions),
    )
