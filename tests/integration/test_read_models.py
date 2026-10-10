"""Control-plane read models (ADR 0012): numbers reconcile with the system of record."""

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import httpx2
import pytest

from forge.api.app import create_app
from forge.domain.execution import Execution
from forge.domain.risk import RiskLevel
from forge.evaluation.service import evaluate_mission
from forge.infrastructure.clock import SystemClock
from forge.runtime.agent_loop import Runtime, claim_next
from forge.runtime.worker import Worker
from tests.integration.conftest import UowFactory
from tests.runtime_doubles import (
    CALL_COST,
    CALL_USAGE,
    RecordingTool,
    ScriptedModel,
    answer,
    runtime,
    submit,
    tool_call,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]
CLOCK = SystemClock()


@pytest.fixture
async def api(uow: UowFactory) -> AsyncIterator[httpx2.AsyncClient]:
    app = create_app(
        uow_factory=uow,
        clock=CLOCK,
        readiness_checks=(),
        readiness_timeout_seconds=1,
        stream_poll_seconds=0.05,
    )
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://forge"
    ) as client:
        yield client


async def run_next(rt: Runtime) -> Execution:
    claim = await claim_next(rt)
    assert claim is not None
    final = await Worker(rt, poll_interval_seconds=0.01).run_claim(claim)
    assert final is not None
    return final


async def completed_mission(uow: UowFactory) -> tuple[UUID, Execution]:
    mission_id = await submit(uow)
    final = await run_next(runtime(uow, ScriptedModel(tool_call(), answer()), RecordingTool()))
    await evaluate_mission(uow, CLOCK, mission_id, trigger="auto")
    return mission_id, final


async def test_the_mission_list_reconciles_spend_tokens_and_progress(
    api: httpx2.AsyncClient, uow: UowFactory
) -> None:
    mission_id, execution = await completed_mission(uow)
    waiting = await submit(uow)

    listed = {m["id"]: m for m in (await api.get("/missions")).json()}
    only_ready = (await api.get("/missions", params={"status": "READY"})).json()

    done = listed[str(mission_id)]
    assert done["status"] == "COMPLETED"
    assert done["cost_micro_usd"] == execution.cost_micro_usd == 2 * CALL_COST + 7
    assert done["input_tokens"] == 2 * CALL_USAGE.input_tokens
    assert (done["tasks_total"], done["tasks_completed"]) == (1, 1)
    assert [m["id"] for m in only_ready] == [str(waiting)]


async def test_insights_aggregate_model_tool_cost_and_status(
    api: httpx2.AsyncClient, uow: UowFactory
) -> None:
    await completed_mission(uow)
    denied = await submit(uow, tools=("metrics.read",), max_risk=RiskLevel.LOW)
    await run_next(runtime(uow, ScriptedModel(tool_call("logs.read"), answer()), RecordingTool()))

    body: dict[str, Any] = (await api.get("/insights")).json()

    (model,) = body["models"]
    assert model["calls"] == 4
    assert model["input_tokens"] == 4 * CALL_USAGE.input_tokens
    assert model["cost_micro_usd"] == 4 * CALL_COST
    assert model["latency_p50_ms"] <= model["latency_p95_ms"] <= model["latency_p99_ms"]
    tools = {t["tool"]: t for t in body["tools"]}
    assert tools["metrics.read"]["calls"] == 1
    assert tools["logs.read"]["denials"] == 1
    assert {c["key"]: c["count"] for c in body["missions_by_status"]} == {"COMPLETED": 2}
    assert body["total_cost_micro_usd"] == 4 * CALL_COST + 7
    assert sum(d["cost_micro_usd"] for d in body["cost_by_day"]) == body["total_cost_micro_usd"]
    assert denied


async def test_agent_usage_and_evaluation_lists(api: httpx2.AsyncClient, uow: UowFactory) -> None:
    mission_id, execution = await completed_mission(uow)

    usage = (await api.get(f"/agents/{execution.agent_id}/usage")).json()
    evaluations = (await api.get("/evaluations")).json()
    runs = (await api.get("/benchmark-runs")).json()

    assert usage["executions"] == 1
    assert usage["cost_micro_usd"] == execution.cost_micro_usd
    assert usage["models"][0]["calls"] == 2
    dimensions = {d["dimension"] for d in usage["dimensions"]}
    assert {"efficiency", "trajectory_quality"} <= dimensions
    assert "correctness" not in dimensions  # unscored without ground truth: not averaged in
    assert [e["mission_id"] for e in evaluations] == [str(mission_id)]
    assert runs == []
    assert (await api.get(f"/agents/{UUID(int=5)}/usage")).status_code == 404


async def test_the_event_stream_delivers_every_event_once_then_ends(
    api: httpx2.AsyncClient, uow: UowFactory
) -> None:
    mission_id, _ = await completed_mission(uow)
    history = (await api.get(f"/missions/{mission_id}/events")).json()

    response = await api.get(f"/missions/{mission_id}/events/stream")

    assert response.headers["content-type"].startswith("text/event-stream")
    frames = [f for f in response.text.split("\n\n") if f and not f.startswith(":")]
    ids = [line[4:] for f in frames for line in f.splitlines() if line.startswith("id: ")]
    assert ids == [e["id"] for e in history]  # all of them, once, in order
    assert frames[-1].startswith("event: end")
    assert (await api.get(f"/missions/{UUID(int=9)}/events/stream")).status_code == 404
