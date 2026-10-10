"""Phase 10 definition of done, against the running stack's UI (http://127.0.0.1:13000).

A planned mission runs from planning through evaluation; the control plane then shows
the whole of it, and the resulting agent performance, as a user would see it. Pages are
server-rendered, so their HTML is exactly what a user's browser receives.
"""

import html
import os
import re
import time
from collections.abc import Iterator
from typing import Any

import httpx2
import pytest

pytestmark = pytest.mark.e2e
API = os.environ.get("FORGE_E2E_API", "http://127.0.0.1:18000")
UI = os.environ.get("FORGE_E2E_UI", "http://127.0.0.1:13000")


@pytest.fixture
def clients() -> Iterator[tuple[httpx2.Client, httpx2.Client]]:
    with (
        httpx2.Client(base_url=API, timeout=10) as api,
        httpx2.Client(base_url=UI, timeout=30) as ui,
    ):
        yield api, ui


def text_of(response: httpx2.Response) -> str:
    """Visible text, roughly: tags stripped, entities decoded."""
    assert response.status_code == 200, (response.request.url, response.status_code)
    return html.unescape(re.sub(r"<[^>]+>", " ", response.text))


def shows(page: str, *texts: str) -> None:
    for expected in texts:
        assert expected in page, expected


def register(api: httpx2.Client, role: str, model: str, capabilities: list[str]) -> str:
    response = api.post(
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


def wait_for(condition: Any, timeout: float = 90.0) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(0.5)
    raise AssertionError("condition not met in time")


def test_a_user_observes_a_mission_from_planning_through_evaluation(
    clients: tuple[httpx2.Client, httpx2.Client],
) -> None:
    api, ui = clients
    planner = register(api, "planner", "demo-planner-1", [])
    register(api, "observer", "demo-investigator-1", ["observability", "incident-response"])
    register(api, "release-analyst", "demo-investigator-1", ["change-management"])
    mission_id = api.post(
        "/missions",
        json={
            "objective": "Investigate why checkout latency increased after yesterday's deployment.",
            "planner_agent_id": planner,
            "budget_micro_usd": 500_000,
        },
    ).json()["id"]
    mission = wait_for(
        lambda: (m := api.get(f"/missions/{mission_id}").json())["status"] == "COMPLETED" and m
    )
    wait_for(lambda: api.get(f"/missions/{mission_id}/evaluations").json())  # evaluated

    # The mission, from planning to evaluation.
    page = text_of(ui.get(f"/missions/{mission_id}"))
    for key in ("plan", "metrics", "changes", "correlate"):
        assert key in page  # the planner's task graph
    shows(page, "Routing", "Routed to")  # capability routing decisions
    assert "demo/demo-investigator-1" in page  # model calls
    shows(page, "metrics.read", "completed")  # tool calls and outcomes
    shows(page, "Cache hit rate", "Cached tokens")
    assert "Conclusion:" in page  # results and evidence
    shows(page, "trajectory quality", "efficiency")  # evaluation
    assert "react-flow" in ui.get(f"/missions/{mission_id}").text  # the DAG is drawn

    # Live events reach the browser through the UI's read-only proxy.
    stream = ui.get(f"/api/forge/missions/{mission_id}/events/stream")
    assert stream.headers["content-type"].startswith("text/event-stream")
    shows(stream.text, "mission.planned", "event: end")
    assert ui.post(f"/api/forge/missions/{mission_id}/cancel").status_code == 405  # no writes

    # The resulting agent performance.
    routed = {t["agent_id"] for t in mission["tasks"] if t["kind"] == "WORK"}
    agent_page = text_of(ui.get(f"/agents/{routed.pop()}"))
    shows(agent_page, "Competency", "Reliability")
    shows(agent_page, "Evaluation dimensions", "Failure clusters")
    shows(agent_page, "Model usage", "demo/demo-investigator-1")

    # Every other page renders from real data.
    assert mission_id[:8] in text_of(ui.get("/"))
    assert mission_id[:8] in text_of(ui.get("/evaluations"))
    assert "Investigate why checkout latency" in text_of(ui.get("/missions?status=COMPLETED"))
    assert "observability" in text_of(ui.get("/capabilities"))
    assert "p95" in text_of(ui.get("/observability"))
    assert "By agent" in text_of(ui.get("/costs"))
    assert "incident-investigation" in text_of(ui.get("/benchmarks"))
    for path in ("/agents", "/skills", "/policies", "/approvals"):
        text_of(ui.get(path))
    assert ui.get(f"/missions/{'0' * 8}-0000-0000-0000-000000000000").status_code == 404
