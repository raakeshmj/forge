"""Phase 8 demonstration against the running Docker Compose stack.

The demo investigator finds the checkout regression and asks to roll back the
deployment, a HIGH-risk, non-idempotent action. The worker parks the execution,
is SIGKILLed and replaced while the request waits, an unauthenticated decision is
refused, an operator approves, and the rollback runs exactly once. Run with `make e2e`.
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
OPERATOR_TOKEN = "e2e-" + "o" * 40
ENV = {**os.environ, "FORGE_OPERATOR_TOKENS": f"e2e-operator:{OPERATOR_TOKEN}"}


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
    compose("up", "-d", "--wait", "--force-recreate", "--no-deps", "api", "worker")
    with httpx2.Client(base_url=API, timeout=10) as client:
        yield client


def test_high_risk_rollback_waits_for_a_human_across_a_worker_kill_and_runs_once(
    client: httpx2.Client,
) -> None:
    agent = client.post(
        "/agents",
        json={
            "name": f"remediator-{time.time_ns()}",
            "role": "incident responder",
            "instructions": "Investigate checkout latency and remediate if the deploy caused it.",
            "model": {"provider": "demo", "name": "demo-investigator-1"},
            "tools": ["metrics.read", "deployment.rollback"],
        },
    ).json()
    mission_id = client.post(
        "/missions",
        json={
            "objective": "Investigate why checkout latency increased after yesterday's deployment.",
            "agent_id": agent["agent_id"],
            "budget_micro_usd": 100_000,
            "max_risk": "HIGH",
        },
    ).json()["id"]

    def pending() -> list[dict[str, Any]]:
        approvals: list[dict[str, Any]] = client.get(
            "/approvals", params={"status": "PENDING"}
        ).json()
        return [a for a in approvals if a["mission_id"] == mission_id]

    (request,) = wait_for(pending)
    assert request["tool"] == "deployment.rollback"
    assert request["risk"] == "HIGH"
    assert request["arguments"]["service"] == "checkout"
    assert "rollback" in request["rationale"]
    assert len(request["evidence"]) == 1  # the metrics result the agent acted on

    # The worker dies while the request waits; a replacement must not run the action.
    compose("kill", "-s", "SIGKILL", "worker")
    compose("up", "-d", "--no-deps", "worker")
    time.sleep(3)
    assert [a["id"] for a in pending()] == [request["id"]]
    assert client.get(f"/missions/{mission_id}").json()["status"] == "WAITING_APPROVAL"

    decision = {"decision": "approve", "reason": "rollback approved by on-call"}
    forged = client.post(f"/approvals/{request['id']}/decision", json=decision)
    assert forged.status_code == 401
    approved = client.post(
        f"/approvals/{request['id']}/decision",
        json=decision,
        headers={"Authorization": f"Bearer {OPERATOR_TOKEN}"},
    )
    assert approved.status_code == 200
    assert approved.json()["decided_by"] == "e2e-operator"

    def finished() -> dict[str, Any] | None:
        mission: dict[str, Any] = client.get(f"/missions/{mission_id}").json()
        return mission if mission["status"] in {"COMPLETED", "FAILED", "CANCELLED"} else None

    mission = wait_for(finished)
    assert mission["status"] == "COMPLETED"
    events = client.get(f"/missions/{mission_id}/events").json()
    rollbacks = [
        e
        for e in events
        if e["event_type"] == "tool.called" and e["payload"]["tool"] == "deployment.rollback"
    ]
    assert len(rollbacks) == 1  # exactly once, after approval
    (execution,) = mission["executions"]
    assert "Remediation: Rolled back checkout" in execution["output"]
    audit = client.get("/audit", params={"mission_id": mission_id}).json()
    trail = [(r["actor_type"], r["action"], r["decision"], r["tool"]) for r in reversed(audit)]
    assert ("agent", "tool.execute", "approval_requested", "deployment.rollback") in trail
    assert ("operator", "approval.decide", "approved", "deployment.rollback") in trail
    assert ("agent", "tool.execute", "allowed", "deployment.rollback") in trail
