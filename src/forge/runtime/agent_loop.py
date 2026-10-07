"""The durable agent loop (ADR 0002).

Each step commits one transaction: the next immutable checkpoint, its events,
and the execution's spend, conditional on still holding the lease fence. A model
response is committed before any tool it asks for runs; a tool result is
committed before the model sees it. Resume continues from the latest
checkpoint, so committed work is never repeated.
"""

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import uuid4

from forge.domain.agent import AgentVersion, ModelRef
from forge.domain.checkpoint import STATE_VERSION, AgentState, Checkpoint
from forge.domain.cost import ModelPrice, Usage
from forge.domain.events import (
    AgentCancelled,
    AgentCompleted,
    AgentFailed,
    AgentStarted,
    CheckpointCreated,
    DomainEvent,
    ModelCalled,
    ModelFailed,
    ToolCalled,
    ToolCompleted,
    ToolDenied,
    ToolFailed,
)
from forge.domain.execution import Execution, ExecutionStatus, FailureKind
from forge.domain.mission import Mission, MissionStatus
from forge.domain.model import ModelRequest, ModelResponse, Role, ToolResultBlock, ToolUseBlock
from forge.domain.repositories import Clock, UnitOfWork, UnitOfWorkFactory
from forge.domain.task import Task
from forge.domain.tool import (
    InvocationStatus,
    ToolDefinition,
    ToolInvocation,
    idempotency_key,
)
from forge.inference.client import ModelCallError, ModelClient
from forge.observability import tracing
from forge.observability.logs import correlation_id
from forge.orchestration import missions
from forge.tools import pipeline
from forge.tools.base import Tool, ToolContext

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Runtime:
    """Everything a worker needs, wired by the composition root."""

    uow_factory: UnitOfWorkFactory
    clock: Clock
    models: Mapping[str, ModelClient]  # by provider
    prices: Mapping[ModelRef, ModelPrice]
    tools: Mapping[str, Tool]
    worker_id: str
    lease_seconds: float
    max_attempts: int = 5  # claims of one execution before recovery is declared exhausted


@dataclass(frozen=True, slots=True)
class Claim:
    execution: Execution
    fence: int


async def claim_next(runtime: Runtime) -> Claim | None:
    """Take over an execution whose lease expired, else start the oldest READY task."""
    now = runtime.clock.now()
    async with runtime.uow_factory() as uow:
        reclaimed = await uow.executions.reclaim_expired(
            worker_id=runtime.worker_id, lease_seconds=runtime.lease_seconds
        )
        if reclaimed is not None:
            execution, fence = reclaimed
            mission = await uow.missions.get(execution.mission_id)
            await _append(uow, mission, _started(execution, runtime.worker_id))
            return Claim(execution, fence)

        task = await uow.tasks.lock_next_ready()
        if task is None:
            return None
        version = await uow.agents.get_current_version(task.agent_id)
        execution = Execution(
            id=uuid4(),
            mission_id=task.mission_id,
            task_id=task.id,
            agent_id=task.agent_id,
            agent_version_id=version.id,
            model=version.model,
            status=ExecutionStatus.RUNNING,
            attempt=1,
            usage=Usage(),
            cost_micro_usd=0,
            output=None,
            failure_kind=None,
            failure_detail=None,
            started_at=now,
            completed_at=None,
        )
        fence = await uow.executions.add(
            execution, worker_id=runtime.worker_id, lease_seconds=runtime.lease_seconds
        )
        await missions.start_task(uow, task, execution.id, now)
        mission = await uow.missions.get(task.mission_id)
        await _append(uow, mission, _started(execution, runtime.worker_id))
        await uow.checkpoints.add(
            Checkpoint(execution.id, 1, AgentState.initial(task.objective), STATE_VERSION, now)
        )
        await _append(uow, mission, _checkpointed(execution, 1))
    return Claim(execution, fence)


class ExecutionRunner:
    """Drives one claimed execution to a terminal state, or until its lease is lost."""

    _task: Task
    _version: AgentVersion
    _state: AgentState
    _sequence: int
    _correlation_id: str

    def __init__(self, runtime: Runtime, claim: Claim) -> None:
        self._rt = runtime
        self._fence = claim.fence
        self._execution = claim.execution
        self.finished = False  # set once the terminal transition has committed

    async def run(self) -> Execution:
        async with self._rt.uow_factory() as uow:
            mission = await uow.missions.get(self._execution.mission_id)
            self._task = await uow.tasks.get(self._execution.task_id)
            self._version = await uow.agents.get_version(self._execution.agent_version_id)
            checkpoint = await uow.checkpoints.latest(self._execution.id)
        if checkpoint is None:
            raise RuntimeError(f"execution {self._execution.id} has no checkpoint")
        self._state = checkpoint.state
        self._sequence = checkpoint.sequence
        self._correlation_id = mission.correlation_id

        token = correlation_id.set(mission.correlation_id)
        try:
            with tracing.agent_span(self._execution, worker_id=self._rt.worker_id) as span:
                _log.info(
                    "execution claimed",
                    extra={
                        "execution_id": str(self._execution.id),
                        "attempt": self._execution.attempt,
                        "checkpoint": self._sequence,
                    },
                )
                if self._execution.attempt > self._rt.max_attempts:
                    final = await self._fail(
                        FailureKind.RECOVERY_EXHAUSTED,
                        f"claimed {self._execution.attempt} times without finishing",
                    )
                else:
                    final = await self._loop()
                tracing.finish_agent_span(span, final)
            return final
        finally:
            correlation_id.reset(token)

    async def _loop(self) -> Execution:
        while True:
            async with self._rt.uow_factory() as uow:
                mission = await uow.missions.get(self._execution.mission_id)
                spent = await uow.executions.mission_spend(mission.id)
            if mission.status is MissionStatus.CANCELLED:
                return await self._cancel()
            if mission.deadline_passed(self._rt.clock.now()):
                return await self._fail(
                    FailureKind.DEADLINE_EXCEEDED, f"deadline {mission.deadline} passed"
                )
            if mission.budget.is_exhausted_by(spent):
                limit = mission.budget.limit_micro_usd
                return await self._fail(
                    FailureKind.BUDGET_EXCEEDED, f"spent {spent} of {limit} micro-USD"
                )

            pending = self._state.pending_tool_uses()
            if pending:
                outcome = await self._tool_step(pending[0], mission)
            elif self._state.turns >= mission.execution_policy.max_steps:
                steps = mission.execution_policy.max_steps
                return await self._fail(
                    FailureKind.MAX_STEPS_EXCEEDED, f"no answer within {steps} model turns"
                )
            else:
                self._require_awaiting_model()
                outcome = await self._model_step(mission)
            if outcome is not None:
                return outcome

    # --- Model ---------------------------------------------------------------------

    async def _model_step(self, mission: Mission) -> Execution | None:
        model = self._execution.model
        client, price = self._rt.models.get(model.provider), self._rt.prices.get(model)
        if client is None or price is None:
            return await self._fail(
                FailureKind.MODEL_FAILED, f"no client or price configured for {model}"
            )
        policy = mission.execution_policy
        request = ModelRequest(
            model=model,
            system=self._version.instructions,
            messages=self._state.messages,
            tools=self._exposed_tools(mission),
            max_output_tokens=policy.max_output_tokens,
        )
        turn = self._state.turns
        failures: list[DomainEvent] = []
        retry = policy.model_retry
        for attempt in range(1, retry.max_attempts + 1):
            response, error = await self._call_model(
                client, request, price, turn, attempt, policy.model_timeout_seconds
            )
            if response is not None:
                return await self._record_model_turn(response, price, turn, attempt, failures)
            error_type, retryable = error
            failures.append(
                ModelFailed(
                    aggregate_id=self._execution.id,
                    mission_id=self._execution.mission_id,
                    turn=turn,
                    attempt=attempt,
                    error_type=error_type,
                    retryable=retryable,
                )
            )
            _log.warning(
                "model call failed",
                extra={
                    "operation": "model.call",
                    "execution_id": str(self._execution.id),
                    "attempt": attempt,
                    "error_type": error_type,
                    "retryable": retryable,
                },
            )
            if not retryable:
                break
            if attempt < retry.max_attempts:
                await asyncio.sleep(retry.backoff_after(attempt))
        detail = f"{failures[-1].payload()['error_type']} after {len(failures)} attempt(s)"
        return await self._fail(FailureKind.MODEL_FAILED, detail, failures)

    async def _call_model(
        self,
        client: ModelClient,
        request: ModelRequest,
        price: ModelPrice,
        turn: int,
        attempt: int,
        limit_seconds: float,
    ) -> tuple[ModelResponse | None, tuple[str, bool]]:
        """One attempt. Returns the response, or the error type and whether it is retryable."""
        model = request.model
        with tracing.tracer().start_as_current_span(
            "forge.model.call",
            attributes={
                "forge.provider": model.provider,
                "forge.model": model.name,
                "forge.turn": turn,
                "forge.attempt": attempt,
            },
        ) as span:
            try:
                async with asyncio.timeout(limit_seconds):
                    response = await client.complete(request)
            except TimeoutError:
                span.set_attribute("forge.status", "timeout")
                return None, ("TimeoutError", True)
            except ModelCallError as exc:
                span.set_attribute("forge.status", "error")
                return None, (type(exc).__name__, exc.retryable)
            usage = response.usage
            span.set_attributes(
                {
                    "forge.status": "ok",
                    "forge.input_tokens": usage.input_tokens,
                    "forge.output_tokens": usage.output_tokens,
                    "forge.cached_tokens": usage.cache_read_input_tokens,
                    "forge.cache_creation_tokens": usage.cache_creation_input_tokens,
                    "forge.cost_micro_usd": price.cost_of(usage),
                    "forge.finish_reason": response.stop_reason,
                }
            )
            return response, ("", False)

    async def _record_model_turn(
        self,
        response: ModelResponse,
        price: ModelPrice,
        turn: int,
        attempts: int,
        failures: Sequence[DomainEvent],
    ) -> Execution | None:
        cost = price.cost_of(response.usage)
        state = self._state.with_model_response(response)
        execution = self._execution.record_spend(response.usage, cost)
        called = ModelCalled(
            aggregate_id=execution.id,
            mission_id=execution.mission_id,
            turn=turn,
            provider=execution.model.provider,
            model=execution.model.name,
            attempts=attempts,
            stop_reason=response.stop_reason,
            usage=response.usage,
            cost_micro_usd=cost,
        )
        if response.tool_uses:
            await self._commit(state, execution, [*failures, called])
            return None
        final = execution.complete(response.text, self._rt.clock.now())
        return await self._finish(final, [*failures, called], state=state)

    def _exposed_tools(self, mission: Mission) -> tuple[ToolDefinition, ...]:
        """Offer the model only tools a call could be authorized for; the pipeline still checks."""
        return tuple(
            self._rt.tools[name].definition
            for name in sorted(self._rt.tools)
            if pipeline.authorize(
                name, tools=self._rt.tools, version=self._version, task=self._task, mission=mission
            ).allowed
        )

    def _require_awaiting_model(self) -> None:
        last = self._state.messages[-1]
        if last.role is Role.ASSISTANT:
            raise RuntimeError(
                f"execution {self._execution.id}: checkpoint {self._sequence} ends with a final "
                "answer but the execution is still running"
            )

    # --- Tools ---------------------------------------------------------------------

    async def _tool_step(self, call: ToolUseBlock, mission: Mission) -> Execution | None:
        turn = self._state.turns - 1  # pending calls always belong to the latest model turn
        key = idempotency_key(self._execution.id, turn, call.id)
        decision = pipeline.authorize(
            call.name, tools=self._rt.tools, version=self._version, task=self._task, mission=mission
        )
        with tracing.tracer().start_as_current_span(
            "forge.tool.call",
            attributes={
                "forge.tool": call.name,
                "forge.turn": turn,
                "forge.permission_result": "allowed" if decision.allowed else "denied",
            },
        ) as span:
            if not decision.allowed:
                await self._deny(call, decision)
                return None
            tool = self._rt.tools[call.name]
            definition = tool.definition
            span.set_attributes(
                {"forge.tool_version": definition.version, "forge.risk": definition.risk.value}
            )

            async with self._rt.uow_factory() as uow:
                previous = await uow.tool_invocations.get(key)
            if previous is not None and previous.status is not InvocationStatus.INTENDED:
                raise RuntimeError(f"tool outcome {key} is recorded but its checkpoint is missing")
            if previous is not None and not definition.idempotency.safe_to_repeat:
                span.set_attribute("forge.status", "outcome_unknown")
                return await self._fail(
                    FailureKind.TOOL_OUTCOME_UNKNOWN,
                    f"{call.name} ({key}) may have run before an interruption and is not "
                    "safe to repeat; reconcile manually",
                )

            invocation = await self._record_intent(call, turn, key, definition)
            context = ToolContext(
                idempotency_key=key,
                execution_id=self._execution.id,
                mission_id=self._execution.mission_id,
                agent_id=self._execution.agent_id,
            )
            outcome = await pipeline.execute(tool, call.arguments, context)
            span.set_attribute("forge.retry_count", outcome.attempts - 1)
            if outcome.result is None:
                span.set_attribute("forge.status", "outcome_unknown")
                return await self._fail(
                    FailureKind.TOOL_OUTCOME_UNKNOWN,
                    f"{call.name} ({key}) timed out and is not safe to repeat; reconcile manually",
                )
            result = outcome.result
            span.set_attributes(
                {
                    "forge.status": "failed" if outcome.failed else "ok",
                    "forge.cost_micro_usd": result.cost_micro_usd,
                }
            )

        event: DomainEvent
        if outcome.failed:
            event = ToolFailed(
                aggregate_id=self._execution.id,
                mission_id=self._execution.mission_id,
                tool_use_id=call.id,
                tool=call.name,
                error_type=outcome.error_type or "",
                attempts=outcome.attempts,
            )
        else:
            event = ToolCompleted(
                aggregate_id=self._execution.id,
                mission_id=self._execution.mission_id,
                tool_use_id=call.id,
                tool=call.name,
                is_error=result.is_error,
                attempts=outcome.attempts,
                cost_micro_usd=result.cost_micro_usd,
            )
        state = self._state.with_tool_result(
            ToolResultBlock(call.id, result.content, is_error=result.is_error or outcome.failed)
        )
        execution = self._execution.record_spend(Usage(), result.cost_micro_usd)
        status = InvocationStatus.FAILED if outcome.failed else InvocationStatus.SUCCEEDED
        await self._commit(state, execution, [event], ledger=(invocation.idempotency_key, status))
        return None

    async def _deny(self, call: ToolUseBlock, decision: pipeline.Decision) -> None:
        stage = decision.stage.value if decision.stage else "unknown"
        denied = ToolDenied(
            aggregate_id=self._execution.id,
            mission_id=self._execution.mission_id,
            tool_use_id=call.id,
            tool=call.name,
            stage=stage,
            reason=decision.reason,
        )
        result = ToolResultBlock(
            call.id, f"Tool call denied at {stage}: {decision.reason}", is_error=True
        )
        await self._commit(self._state.with_tool_result(result), self._execution, [denied])

    async def _record_intent(
        self, call: ToolUseBlock, turn: int, key: str, definition: ToolDefinition
    ) -> ToolInvocation:
        """Commit the intent before executing, so a crash leaves evidence the call may have run."""
        async with self._rt.uow_factory() as uow:
            await uow.executions.update(
                self._execution, fence=self._fence, lease_seconds=self._rt.lease_seconds
            )
            invocation = await uow.tool_invocations.record_intent(
                ToolInvocation(
                    idempotency_key=key,
                    execution_id=self._execution.id,
                    turn=turn,
                    tool_use_id=call.id,
                    tool=call.name,
                    tool_version=definition.version,
                    status=InvocationStatus.INTENDED,
                    invocations=1,
                )
            )
            await self._append_execution_events(
                uow,
                [
                    ToolCalled(
                        aggregate_id=self._execution.id,
                        mission_id=self._execution.mission_id,
                        turn=turn,
                        tool_use_id=call.id,
                        tool=call.name,
                        tool_version=definition.version,
                        idempotency_key=key,
                        invocation=invocation.invocations,
                    )
                ],
            )
        return invocation

    # --- Commits -------------------------------------------------------------------

    async def _commit(
        self,
        state: AgentState,
        execution: Execution,
        events: Sequence[DomainEvent],
        *,
        ledger: tuple[str, InvocationStatus] | None = None,
    ) -> None:
        """One step: fenced execution update, next checkpoint, events. All or nothing."""
        sequence = self._sequence + 1
        async with self._rt.uow_factory() as uow:
            await uow.executions.update(
                execution, fence=self._fence, lease_seconds=self._rt.lease_seconds
            )
            if ledger is not None:
                await uow.tool_invocations.record_outcome(*ledger)
            await self._write_checkpoint(uow, state, sequence, events)
        self._state, self._execution, self._sequence = state, execution, sequence

    async def _write_checkpoint(
        self, uow: UnitOfWork, state: AgentState, sequence: int, events: Sequence[DomainEvent]
    ) -> None:
        now = self._rt.clock.now()
        await uow.checkpoints.add(
            Checkpoint(self._execution.id, sequence, state, STATE_VERSION, now)
        )
        await self._append_execution_events(
            uow, [*events, _checkpointed(self._execution, sequence)]
        )

    async def _fail(
        self, kind: FailureKind, detail: str, events: Sequence[DomainEvent] = ()
    ) -> Execution:
        _log.warning(
            "execution failed",
            extra={
                "operation": "execution",
                "execution_id": str(self._execution.id),
                "attempt": self._execution.attempt,
                "failure_kind": kind.value,
                "retryable": False,
            },
        )
        return await self._finish(self._execution.fail(kind, detail, self._rt.clock.now()), events)

    async def _cancel(self) -> Execution:
        return await self._finish(self._execution.cancel(self._rt.clock.now()), ())

    async def _finish(
        self,
        final: Execution,
        events: Sequence[DomainEvent],
        *,
        state: AgentState | None = None,
    ) -> Execution:
        """Terminal transition of execution, task, and mission in one transaction.

        The mission row lock serialises this against cancellation: a mission
        cancelled while the last step ran ends CANCELLED, never COMPLETED.
        """
        now = self._rt.clock.now()
        async with self._rt.uow_factory() as uow:
            mission = await missions.lock_mission(uow, final.mission_id)
            cancelled = mission.status is MissionStatus.CANCELLED
            if cancelled and final.status is not ExecutionStatus.CANCELLED:
                final = self._execution.cancel(now)
            await uow.executions.update(final, fence=self._fence, lease_seconds=0)
            if state is not None:
                await self._write_checkpoint(uow, state, self._sequence + 1, events)
                events = ()
            await self._append_execution_events(uow, [*events, _terminal(final, state)])
            if final.status is not ExecutionStatus.CANCELLED:
                mission = await missions.finish_task(uow, mission, final, now)
        self.finished = True
        self._execution = final

        if mission.status.is_terminal:
            async with self._rt.uow_factory() as uow:
                tasks = await uow.tasks.list_for_mission(mission.id)
                executions = await uow.executions.list_for_mission(mission.id)
                spent = await uow.executions.mission_spend(mission.id)
            tracing.record_finished_mission(
                mission, tasks, cost_micro_usd=spent, executions=executions
            )
        _log.info(
            "execution finished",
            extra={"execution_id": str(final.id), "status": final.status.value},
        )
        return final

    async def _append_execution_events(
        self, uow: UnitOfWork, events: Sequence[DomainEvent]
    ) -> None:
        for event in events:
            await uow.events.append(event, correlation_id=self._correlation_id)


def _started(execution: Execution, worker_id: str) -> AgentStarted:
    return AgentStarted(
        aggregate_id=execution.id,
        mission_id=execution.mission_id,
        attempt=execution.attempt,
        worker_id=worker_id,
        agent_id=execution.agent_id,
        agent_version_id=execution.agent_version_id,
    )


def _checkpointed(execution: Execution, sequence: int) -> CheckpointCreated:
    return CheckpointCreated(
        aggregate_id=execution.id,
        mission_id=execution.mission_id,
        sequence=sequence,
        state_version=STATE_VERSION,
    )


def _terminal(execution: Execution, state: AgentState | None) -> DomainEvent:
    if execution.status is ExecutionStatus.COMPLETED:
        return AgentCompleted(
            aggregate_id=execution.id,
            mission_id=execution.mission_id,
            turns=state.turns if state is not None else 0,
            usage=execution.usage,
            cost_micro_usd=execution.cost_micro_usd,
        )
    if execution.status is ExecutionStatus.FAILED:
        return AgentFailed(
            aggregate_id=execution.id,
            mission_id=execution.mission_id,
            failure_kind=execution.failure_kind.value if execution.failure_kind else "",
            detail=execution.failure_detail or "",
        )
    return AgentCancelled(aggregate_id=execution.id, mission_id=execution.mission_id)


async def _append(uow: UnitOfWork, mission: Mission, event: DomainEvent) -> None:
    await uow.events.append(event, correlation_id=mission.correlation_id)
