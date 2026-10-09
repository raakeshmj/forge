"""Governance over HTTP: an operator decides, writes policy, enables CRITICAL; all audited."""

from collections.abc import AsyncIterator
from uuid import UUID

import httpx2
import pytest

from forge.api.app import create_app
from forge.domain.execution import ExecutionStatus
from forge.domain.risk import RiskLevel
from forge.domain.tool import Idempotency
from forge.infrastructure.clock import SystemClock
from forge.runtime.agent_loop import Runtime, claim_next
from forge.runtime.worker import Worker
from tests.integration.conftest import UowFactory
from tests.runtime_doubles import RecordingTool, ScriptedModel, answer, runtime, submit, tool_call

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

TOKEN = "op-" + "z" * 40
OPERATOR = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
async def api(uow: UowFactory) -> AsyncIterator[httpx2.AsyncClient]:
    app = create_app(
        uow_factory=uow,
        clock=SystemClock(),
        readiness_checks=(),
        readiness_timeout_seconds=1,
        operator_tokens={TOKEN: "alice"},
    )
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://forge"
    ) as client:
        yield client


async def run_next(rt: Runtime) -> ExecutionStatus:
    claim = await claim_next(rt)
    assert claim is not None
    final = await Worker(rt, poll_interval_seconds=0.01).run_claim(claim)
    assert final is not None
    return final.status


async def test_an_operator_approves_over_http_and_the_call_runs(
    api: httpx2.AsyncClient, uow: UowFactory
) -> None:
    restart = RecordingTool(
        name="deployment.restart", risk=RiskLevel.HIGH, idempotency=Idempotency.NON_IDEMPOTENT
    )
    model = ScriptedModel(tool_call("deployment.restart", service="checkout"), answer("ok"))
    mission_id = await submit(uow, tools=("deployment.restart",), max_risk=RiskLevel.HIGH)
    rt = runtime(uow, model, restart)
    assert await run_next(rt) is ExecutionStatus.WAITING_APPROVAL

    (pending,) = (await api.get("/approvals", params={"status": "PENDING"})).json()
    assert pending["mission_id"] == str(mission_id)
    assert pending["arguments"] == {"service": "checkout"}
    assert pending["risk"] == "HIGH"
    decided = await api.post(
        f"/approvals/{pending['id']}/decision",
        json={"decision": "approve", "reason": "maintenance window"},
        headers=OPERATOR,
    )
    again = await api.post(
        f"/approvals/{pending['id']}/decision",
        json={"decision": "deny", "reason": "too late"},
        headers=OPERATOR,
    )

    assert decided.status_code == 200
    assert decided.json()["decided_by"] == "alice"  # the token's operator, not a claimed name
    assert again.status_code == 409
    assert await run_next(rt) is ExecutionStatus.COMPLETED
    assert len(restart.invocations) == 1
    audit = (await api.get("/audit", params={"mission_id": str(mission_id)})).json()
    assert [(r["actor_type"], r["action"], r["decision"]) for r in reversed(audit)] == [
        ("agent", "tool.execute", "approval_requested"),
        ("operator", "approval.decide", "approved"),
        ("agent", "tool.execute", "allowed"),
    ]


async def test_policies_are_versioned_and_audited(api: httpx2.AsyncClient) -> None:
    body = {
        "name": "tickets",
        "rules": [
            {
                "effect": "allow",
                "tools": ["ticket.*"],
                "conditions": [{"argument": "project", "operator": "eq", "value": "payments"}],
            }
        ],
    }
    first = await api.post("/policies", json=body, headers=OPERATOR)
    second = await api.post("/policies", json={**body, "rules": []}, headers=OPERATOR)
    invalid = await api.post(
        "/policies",
        json={"name": "x", "rules": [{"effect": "maybe", "tools": ["*"]}]},
        headers=OPERATOR,
    )

    assert (first.status_code, first.json()["version"]) == (201, 1)
    assert second.json()["version"] == 2
    assert invalid.status_code == 422
    (active,) = (await api.get("/policies")).json()
    assert (active["version"], active["rules"]) == (2, [])  # the newest version governs
    audit = (await api.get("/audit")).json()
    assert [(r["actor"], r["target"]) for r in audit if r["action"] == "policy.write"] == [
        ("alice", "tickets@2"),
        ("alice", "tickets@1"),
    ]


async def test_critical_enablement_is_an_audited_operator_switch(api: httpx2.AsyncClient) -> None:
    enable = await api.post(
        "/tools/database.drop/critical",
        json={"enabled": True, "reason": "migration window"},
        headers=OPERATOR,
    )
    enabled = (await api.get("/critical-tools")).json()
    await api.post(
        "/tools/database.drop/critical",
        json={"enabled": False, "reason": "window closed"},
        headers=OPERATOR,
    )

    assert enable.status_code == 204
    assert enabled == ["database.drop"]
    assert (await api.get("/critical-tools")).json() == []
    decisions = [r["decision"] for r in (await api.get("/audit")).json() if r["tool"]]
    assert decisions == ["disabled", "enabled"]


async def test_unknown_approval_is_not_found(api: httpx2.AsyncClient) -> None:
    missing = UUID(int=1)
    response = await api.post(
        f"/approvals/{missing}/decision",
        json={"decision": "approve", "reason": "x"},
        headers=OPERATOR,
    )
    assert response.status_code == 404
