"""Agent and mission endpoints against a real database."""

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx2
import pytest

from forge.api.app import create_app
from forge.infrastructure.clock import SystemClock
from tests.integration.conftest import UowFactory

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

AGENT = {
    "name": "investigator",
    "role": "incident investigator",
    "instructions": "Investigate using the tools available.",
    "model": {"provider": "demo", "name": "demo-investigator-1"},
    "tools": ["metrics.read"],
}


@pytest.fixture
async def client(uow: UowFactory) -> AsyncIterator[httpx2.AsyncClient]:
    app = create_app(
        uow_factory=uow, clock=SystemClock(), readiness_checks=(), readiness_timeout_seconds=1
    )
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://forge") as client:
        yield client


async def _agent(client: httpx2.AsyncClient, **overrides: Any) -> str:
    response = await client.post("/agents", json={**AGENT, **overrides})
    assert response.status_code == 201, response.text
    return str(response.json()["agent_id"])


async def test_submitting_a_mission_plans_one_ready_task(client: httpx2.AsyncClient) -> None:
    agent_id = await _agent(client)

    response = await client.post(
        "/missions",
        json={"objective": "Why is checkout slow?", "agent_id": agent_id, "budget_micro_usd": 5000},
        headers={"X-Correlation-ID": "req-42"},
    )

    assert response.status_code == 201, response.text
    mission = response.json()
    assert mission["status"] == "READY"
    assert mission["correlation_id"] == "req-42"
    assert [t["status"] for t in mission["tasks"]] == ["READY"]
    assert mission["tasks"][0]["allowed_tools"] == ["metrics.read"]
    assert mission["executions"] == []

    events = (await client.get(f"/missions/{mission['id']}/events")).json()
    assert [e["event_type"] for e in events] == [
        "mission.created",
        "task.created",
        "mission.planned",
    ]
    assert {e["correlation_id"] for e in events} == {"req-42"}


async def test_cancelling_a_mission_cancels_its_task_and_is_final(
    client: httpx2.AsyncClient,
) -> None:
    agent_id = await _agent(client)
    mission_id = (
        await client.post(
            "/missions", json={"objective": "o", "agent_id": agent_id, "budget_micro_usd": 1}
        )
    ).json()["id"]

    cancelled = await client.post(f"/missions/{mission_id}/cancel")
    again = await client.post(f"/missions/{mission_id}/cancel")

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"
    assert [t["status"] for t in cancelled.json()["tasks"]] == ["CANCELLED"]
    assert again.status_code == 409


async def test_duplicate_agent_names_conflict(client: httpx2.AsyncClient) -> None:
    await _agent(client, name="unique")

    response = await client.post("/agents", json={**AGENT, "name": "unique"})

    assert response.status_code == 409


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/missions", {"objective": "o", "agent_id": str(uuid4()), "budget_micro_usd": 1}),
    ],
)
async def test_unknown_references_are_404(
    client: httpx2.AsyncClient, path: str, body: dict[str, Any]
) -> None:
    assert (await client.post(path, json=body)).status_code == 404
    assert (await client.get(f"/missions/{uuid4()}")).status_code == 404


@pytest.mark.parametrize(
    "overrides",
    [
        {"budget_micro_usd": 0},
        {"objective": ""},
        {"deadline": "2026-10-07T12:00:00"},  # naive timestamps are ambiguous
        {"max_risk": "EXTREME"},
        {"unexpected": True},
    ],
)
async def test_invalid_missions_are_rejected(
    client: httpx2.AsyncClient, overrides: dict[str, Any]
) -> None:
    agent_id = await _agent(client)
    body = {"objective": "o", "agent_id": agent_id, "budget_micro_usd": 1, **overrides}

    assert (await client.post("/missions", json=body)).status_code == 422
