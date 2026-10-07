"""Phase 4 demonstration on the running stack: a planner agent plans an incident
investigation; capability routing distributes it across specialists; two evidence
tasks run in parallel and fan in to a correlation task."""

import os
import time
from collections.abc import Iterator
from typing import Any

import httpx2
import pytest

pytestmark = pytest.mark.e2e
API = os.environ.get("FORGE_E2E_API", "http://127.0.0.1:18000")


@pytest.fixture
def client() -> Iterator[httpx2.Client]:
    with httpx2.Client(base_url=API, timeout=10) as client:
        yield client


def register(client: httpx2.Client, role: str, model: str, capabilities: list[str]) -> str:
    response = client.post(
        "/agents",
        json={
            "name": f"{role}-{time.time_ns()}",
            "role": role,
            "instructions": f"You are the {role}. Investigate with the tools available.",
            "model": {"provider": "demo", "name": model},
            "tools": ["metrics.read"],
            "capabilities": capabilities,
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["agent_id"])


def test_planned_mission_fans_out_to_specialists_and_fans_in(client: httpx2.Client) -> None:
    planner = register(client, "planner", "demo-planner-1", [])
    # The stack's database outlives test runs, so agents from earlier runs may also be
    # eligible; the property under test is that the work is spread across specialists.
    register(client, "observer", "demo-investigator-1", ["observability", "incident-response"])
    register(client, "release-analyst", "demo-investigator-1", ["change-management"])
    mission_id = client.post(
        "/missions",
        json={
            "objective": "Investigate why checkout latency increased after yesterday's deployment.",
            "planner_agent_id": planner,
            "budget_micro_usd": 500_000,
        },
    ).json()["id"]

    deadline = time.monotonic() + 60
    mission: dict[str, Any] = {}
    while time.monotonic() < deadline:
        mission = client.get(f"/missions/{mission_id}").json()
        if mission["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            break
        time.sleep(0.25)

    assert mission["status"] == "COMPLETED", mission
    tasks = {task["key"]: task for task in mission["tasks"]}
    assert set(tasks) == {"plan", "metrics", "changes", "correlate"}
    assert len({tasks[k]["agent_id"] for k in ("metrics", "changes")}) == 2
    assert set(tasks["correlate"]["dependencies"]) == {
        tasks["metrics"]["id"],
        tasks["changes"]["id"],
    }
    print(  # noqa: T201 - the demo's narrative output
        f"\nmission {mission_id}: {mission['status']}; tasks "
        + ", ".join(f"{k}->{t['status']}" for k, t in tasks.items())
    )
