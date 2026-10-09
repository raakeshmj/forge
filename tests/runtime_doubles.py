"""Test doubles for the runtime: a scripted model, a recording tool, and crash injection."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from forge.domain.agent import ModelRef
from forge.domain.common import JSONValue
from forge.domain.cost import ModelPrice, Usage
from forge.domain.mission import Budget, ExecutionPolicy, RiskPolicy
from forge.domain.model import ModelRequest, ModelResponse, TextBlock, ToolUseBlock
from forge.domain.risk import RiskLevel
from forge.domain.tool import Idempotency, ToolDefinition, ToolResult
from forge.inference.client import ModelCallError, ModelClient
from forge.inference.profiles import CachingMode, ModelProfile
from forge.infrastructure.clock import SystemClock
from forge.orchestration.agents import register_agent
from forge.orchestration.missions import MissionRequest, submit_mission
from forge.runtime.agent_loop import Runtime
from forge.tools.base import Tool, ToolContext, ToolExecutionError
from tests.integration.conftest import UowFactory

TEST_MODEL = ModelRef("test", "scripted-1")
# 1 micro-USD per input token, 2 per output token: Usage(100, 50) costs 200.
TEST_PRICE = ModelPrice(input=1_000_000, output=2_000_000, cache_read=0, cache_write=0)
TEST_PROFILE = ModelProfile(TEST_MODEL, 100_000, 4_096, TEST_PRICE, True, CachingMode.NONE, 0, 1)
CALL_USAGE = Usage(input_tokens=100, output_tokens=50)
CALL_COST = 200
FAST_POLICY = ExecutionPolicy(retry_initial_backoff_seconds=0, retry_max_backoff_seconds=0)


class SimulatedCrash(BaseException):
    """Escapes every `except Exception`, like the process dying at that instant."""


type Step = ModelResponse | BaseException | Callable[[ModelRequest], Awaitable[ModelResponse]]


def tool_call(
    name: str = "metrics.read", call_id: str = "c1", **arguments: JSONValue
) -> ModelResponse:
    return ModelResponse(
        content=(TextBlock("checking"), ToolUseBlock(call_id, name, arguments)),
        stop_reason="tool_use",
        usage=CALL_USAGE,
    )


def answer(text: str = "done") -> ModelResponse:
    return ModelResponse(content=(TextBlock(text),), stop_reason="end_turn", usage=CALL_USAGE)


async def crash(_: ModelRequest) -> ModelResponse:
    """The worker dies mid-call: the provider may have answered, but nothing was recorded."""
    raise SimulatedCrash


async def hang(_: ModelRequest) -> ModelResponse:
    await asyncio.sleep(3600)
    raise AssertionError("unreachable")


class ScriptedModel:
    def __init__(self, *steps: Step) -> None:
        self._steps = list(steps)
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self._steps:
            raise AssertionError("model called more times than scripted")
        step = self._steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            return await step(request)
        return step


@dataclass
class RecordingTool:
    """Records every invocation and applies a side effect, deduplicated by key when IDEMPOTENT."""

    name: str = "metrics.read"
    idempotency: Idempotency = Idempotency.READ_ONLY
    risk: RiskLevel = RiskLevel.LOW
    max_attempts: int = 1
    timeout_seconds: float = 5.0
    deferred: bool = False
    description: str | None = None
    input_schema: dict[str, JSONValue] | None = None
    behaviours: list[str] = field(default_factory=list)  # consumed one per invocation
    invocations: list[str] = field(default_factory=list)
    effects: dict[str, int] = field(default_factory=dict)
    on_invoke: Callable[[], Awaitable[None]] | None = None

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            version="1",
            description=self.description or f"{self.name} test tool",
            input_schema=self.input_schema or {"type": "object"},
            risk=self.risk,
            idempotency=self.idempotency,
            timeout_seconds=self.timeout_seconds,
            max_attempts=self.max_attempts,
            retry_initial_backoff_seconds=0,
            retry_max_backoff_seconds=0,
            deferred=self.deferred,
        )

    async def invoke(self, arguments: Mapping[str, JSONValue], context: ToolContext) -> ToolResult:
        self.invocations.append(context.idempotency_key)
        behaviour = self.behaviours.pop(0) if self.behaviours else "ok"
        if self.on_invoke is not None:
            await self.on_invoke()
        if behaviour == "hang":
            await asyncio.sleep(3600)
        if behaviour == "fail_retryable":
            raise ToolExecutionError("transient", retryable=True)
        if behaviour == "fail_permanent":
            raise ToolExecutionError("rejected", retryable=False)
        if self.idempotency is Idempotency.IDEMPOTENT:
            self.effects.setdefault(context.idempotency_key, 1)
        elif self.idempotency is Idempotency.NON_IDEMPOTENT:
            self.effects[context.idempotency_key] = self.effects.get(context.idempotency_key, 0) + 1
        if behaviour == "crash_after_effect":
            raise SimulatedCrash
        return ToolResult(f"{self.name} ok {dict(arguments)}", cost_micro_usd=7)


def runtime(
    uow: UowFactory,
    model: ModelClient,
    *tools: Tool,
    worker_id: str = "w1",
    lease_seconds: float = 30.0,
) -> Runtime:
    return Runtime(
        uow_factory=uow,
        clock=SystemClock(),
        models={TEST_MODEL.provider: model},
        profiles={TEST_MODEL: TEST_PROFILE},
        tools={tool.definition.name: tool for tool in tools},
        worker_id=worker_id,
        lease_seconds=lease_seconds,
    )


async def submit(
    uow: UowFactory,
    *,
    tools: tuple[str, ...] = ("metrics.read",),
    budget: int = 1_000_000,
    policy: ExecutionPolicy = FAST_POLICY,
    max_risk: RiskLevel = RiskLevel.LOW,
    agent_name: str | None = None,
    **request: Any,
) -> UUID:
    clock = SystemClock()
    version = await register_agent(
        uow,
        clock,
        name=agent_name or f"agent-{uuid4().hex[:12]}",
        role="investigator",
        instructions="Investigate.",
        model=TEST_MODEL,
        tools=tools,
        correlation_id="corr-test",
    )
    mission = await submit_mission(
        uow,
        clock,
        MissionRequest(
            objective="Why is checkout slow?",
            agent_id=version.agent_id,
            budget=Budget(budget),
            execution_policy=policy,
            risk_policy=RiskPolicy(max_risk),
            **request,
        ),
        correlation_id="corr-test",
    )
    return mission.id


async def expire_lease(engine: AsyncEngine, execution_id: UUID) -> None:
    """Fast-forward past the lease instead of waiting for it."""
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE executions SET lease_expires_at = now() - interval '1s' WHERE id = :id"),
            {"id": execution_id},
        )


class TaskAwareModel:
    """Answers by task: the first line of a task's brief selects its behaviour.

    Behaviours per task key (consumed in order): "fail" (permanent model error),
    "crash" (SimulatedCrash), "parallel" (block until another call is in flight).
    A planning brief is answered with `plan`.
    """

    def __init__(self, behaviours: dict[str, list[str]] | None = None, plan: str = "") -> None:
        self._behaviours = behaviours or {}
        self._plan = plan
        self.calls: list[str] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self._peer = asyncio.Event()

    async def complete(self, request: ModelRequest) -> ModelResponse:
        first = request.messages[0].content[0]
        key = first.text.splitlines()[0] if isinstance(first, TextBlock) else ""
        self.calls.append(key)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        if self.in_flight >= 2:
            self._peer.set()
        try:
            pending = self._behaviours.get(key, [])
            behaviour = pending.pop(0) if pending else "ok"
            if behaviour == "fail":
                raise ModelCallError("rejected", retryable=False)
            if behaviour == "crash":
                raise SimulatedCrash
            if behaviour == "parallel":
                await asyncio.wait_for(self._peer.wait(), timeout=5)
            if key.startswith("Plan this mission"):
                return answer(self._plan)
            return answer(f"result of {key}")
        finally:
            self.in_flight -= 1


class OracleInvestigator:
    """Investigates the service named in the objective and answers correctly (a strong agent)."""

    ANSWERS: ClassVar[dict[str, str]] = {
        "checkout": "Checkout latency regressed after the deploy: p95 880ms. Confidence: 0.9",
        "payments": "Payments: no regression after the deploy (191ms). Confidence: 0.9",
        "inventory": "Inventory spiked at 09:00 to 640ms, before the deploy. Confidence: 0.9",
    }

    async def complete(self, request: ModelRequest) -> ModelResponse:
        first = request.messages[0].content[0]
        objective = first.text.lower() if isinstance(first, TextBlock) else ""
        service = next((s for s in self.ANSWERS if s in objective), "checkout")
        if len(request.messages) == 1:
            return tool_call(service=service, metric="latency_p95_ms")
        return answer(self.ANSWERS[service])
