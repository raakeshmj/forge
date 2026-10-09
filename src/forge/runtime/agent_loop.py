"""The durable agent loop (ADR 0002).

Each step commits one transaction: the next immutable checkpoint, its events,
and the execution's spend, conditional on still holding the lease fence. A model
response is committed before any tool it asks for runs; a tool result is
committed before the model sees it. Resume continues from the latest
checkpoint, so committed work is never repeated.
"""

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import uuid4

from forge.domain.agent import AgentVersion, ModelRef
from forge.domain.checkpoint import STATE_VERSION, AgentState, Checkpoint
from forge.domain.context import ContextPlan
from forge.domain.cost import Usage
from forge.domain.events import (
    AgentCancelled,
    AgentCompleted,
    AgentFailed,
    AgentStarted,
    CheckpointCreated,
    DomainEvent,
    ModelCalled,
    ModelFailed,
    ModelRouted,
    ToolCalled,
    ToolCompleted,
    ToolDenied,
    ToolFailed,
)
from forge.domain.execution import Execution, ExecutionStatus, FailureKind
from forge.domain.mission import Mission
from forge.domain.model import ModelRequest, ModelResponse, Role, ToolResultBlock, ToolUseBlock
from forge.domain.repositories import Clock, UnitOfWork, UnitOfWorkFactory
from forge.domain.skill import SkillVersion
from forge.domain.task import Task, TaskStatus
from forge.domain.tool import (
    InvocationStatus,
    ToolDefinition,
    ToolInvocation,
    idempotency_key,
)
from forge.inference.client import ModelCallError, ModelClient
from forge.inference.context import (
    ContextExceededError,
    PlanningInputs,
    build_request,
    plan_cache,
    plan_context,
    stable_blocks,
)
from forge.inference.profiles import ModelProfile
from forge.inference.router import RoutingDecision, route
from forge.inference.tokens import estimate_messages
from forge.observability import tracing
from forge.observability.logs import correlation_id
from forge.orchestration import budget, missions
from forge.tools import pipeline
from forge.tools.base import Tool, ToolContext
from forge.tools.search import loaded_tools

_log = logging.getLogger(__name__)

RESERVATION_WAIT_SECONDS = 60.0
RESERVATION_POLL_SECONDS = 0.1


@dataclass(frozen=True, slots=True)
class Runtime:
    """Everything a worker needs, wired by the composition root."""

    uow_factory: UnitOfWorkFactory
    clock: Clock
    models: Mapping[str, ModelClient]  # by provider
    profiles: Mapping[ModelRef, ModelProfile]
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
        if task.agent_id is None:  # READY tasks are always routed (database constraint)
            raise RuntimeError(f"task {task.id} is READY but has no agent")
        if task.agent_version_id is not None:
            version = await uow.agents.get_version(task.agent_version_id)
        else:
            version = await uow.agents.get_current_version(task.agent_id)
        mission = await uow.missions.get(task.mission_id)
        initial = AgentState.initial(await missions.task_brief(uow, task))
        skills = tuple([await uow.skills.get(ref.name, ref.version) for ref in version.skills])
        decision, required_tokens = _route(runtime, version, task, mission, initial, skills)
        execution = Execution(
            id=uuid4(),
            mission_id=task.mission_id,
            task_id=task.id,
            agent_id=task.agent_id,
            agent_version_id=version.id,
            model=decision.selected or version.model,
            status=ExecutionStatus.RUNNING,
            attempt=1,
            usage=Usage(),
            cost_micro_usd=0,
            output=None,
            failure_kind=None,
            failure_detail=None,
            started_at=now,
            completed_at=None,
            task_attempt=task.attempts + 1,
        )
        fence = await uow.executions.add(
            execution, worker_id=runtime.worker_id, lease_seconds=runtime.lease_seconds
        )
        await missions.start_task(uow, task, execution.id, now)
        await _append(uow, mission, _started(execution, runtime.worker_id))
        await _append(uow, mission, _routed(execution, decision, required_tokens))
        await uow.checkpoints.add(Checkpoint(execution.id, 1, initial, STATE_VERSION, now))
        await _append(uow, mission, _checkpointed(execution, 1))
        if decision.selected is None:
            # Nothing can serve this execution: finish it now rather than hand it to a runner.
            failed = execution.fail(FailureKind.NO_ELIGIBLE_MODEL, decision.explanation, now)
            await uow.executions.update(failed, fence=fence, lease_seconds=0)
            await _append(uow, mission, _terminal(failed, None))
            mission = await missions.lock_mission(uow, mission.id)
            await missions.finish_task(uow, mission, failed, now)
            _log.warning(
                "no eligible model", extra={"execution_id": str(execution.id), "retryable": False}
            )
            return None
    return Claim(execution, fence)


def authorized_tools(
    runtime: Runtime, version: AgentVersion, task: Task, mission: Mission
) -> tuple[ToolDefinition, ...]:
    """Every tool a call could be authorized for, sorted by name; the pipeline still checks."""
    return tuple(
        runtime.tools[name].definition
        for name in sorted(runtime.tools)
        if pipeline.authorize(
            name, tools=runtime.tools, version=version, task=task, mission=mission
        ).allowed
    )


def exposed_tools(
    runtime: Runtime,
    version: AgentVersion,
    task: Task,
    mission: Mission,
    state: AgentState | None = None,
) -> tuple[ToolDefinition, ...]:
    """Eager tools, then deferred tools in the order `tools.search` loaded them (ADR 0005).

    Loaded tools are appended, never interleaved, so earlier tools keep their positions.
    """
    authorized = authorized_tools(runtime, version, task, mission)
    by_name = {tool.name: tool for tool in authorized}
    loaded = loaded_tools(state) if state is not None else ()
    return (
        *(tool for tool in authorized if not tool.deferred),
        *(by_name[name] for name in loaded if name in by_name and by_name[name].deferred),
    )


def _route(
    runtime: Runtime,
    version: AgentVersion,
    task: Task,
    mission: Mission,
    state: AgentState,
    skills: tuple[SkillVersion, ...],
) -> tuple[RoutingDecision, int]:
    """Choose the execution's model once, before any context is built (ARCHITECTURE §10)."""
    tools = exposed_tools(runtime, version, task, mission)
    inputs = PlanningInputs(version, mission, tools, state.messages, skills)
    required = sum(b.token_estimate for b in stable_blocks(inputs)) + estimate_messages(
        inputs.messages
    )
    decision = route(
        version.model_candidates,
        profiles=runtime.profiles,
        available_providers=set(runtime.models),
        required_context_tokens=required,
        reserved_output_tokens=mission.execution_policy.max_output_tokens,
        needs_tools=bool(tools),
        remaining_budget_micro_usd=mission.budget.limit_micro_usd,
    )
    return decision, required


class ExecutionRunner:
    """Drives one claimed execution to a terminal state, or until its lease is lost."""

    _task: Task
    _version: AgentVersion
    _state: AgentState
    _sequence: int
    _correlation_id: str
    _plan: ContextPlan
    _skills: tuple[SkillVersion, ...]

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
            self._skills = tuple(
                [await uow.skills.get(ref.name, ref.version) for ref in self._version.skills]
            )
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
                task = await uow.tasks.get(self._execution.task_id)
                spent = await uow.executions.mission_spend(mission.id)
            if mission.status.is_terminal or task.status is TaskStatus.CANCELLED:
                return await self._cancel()  # cancelled, or another task already decided it
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
        client, profile = self._rt.models.get(model.provider), self._rt.profiles.get(model)
        if client is None or profile is None:
            return await self._fail(
                FailureKind.MODEL_FAILED, f"no client or profile configured for {model}"
            )
        policy = mission.execution_policy
        inputs = PlanningInputs(
            self._version,
            mission,
            exposed_tools(self._rt, self._version, self._task, mission, self._state),
            self._state.messages,
            self._skills,
        )
        output_tokens = min(policy.max_output_tokens, profile.max_output_tokens)
        try:
            plan, messages = plan_context(
                inputs, budget_tokens=profile.context_window_tokens - output_tokens
            )
        except ContextExceededError as exc:
            return await self._fail(FailureKind.CONTEXT_EXCEEDED, str(exc))
        plan, hints = plan_cache(plan, profile)
        request = build_request(
            plan, hints, messages, inputs=inputs, profile=profile, max_output_tokens=output_tokens
        )
        self._plan = plan
        reserved = await self._reserve(
            mission,
            profile.price.cost_of(
                Usage(input_tokens=plan.total_tokens, output_tokens=output_tokens)
            ),
        )
        if reserved is not None:
            return reserved
        turn = self._state.turns
        failures: list[DomainEvent] = []
        retry = policy.model_retry
        for attempt in range(1, retry.max_attempts + 1):
            started = time.perf_counter()
            response, error = await self._call_model(
                client, request, profile, turn, attempt, policy.model_timeout_seconds
            )
            if response is not None:
                latency_ms = round((time.perf_counter() - started) * 1000)
                return await self._record_model_turn(
                    response, profile, turn, attempt, failures, latency_ms
                )
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
        profile: ModelProfile,
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
                    "forge.cost_micro_usd": profile.price.cost_of(usage),
                    "forge.finish_reason": response.stop_reason,
                }
            )
            return response, ("", False)

    async def _record_model_turn(
        self,
        response: ModelResponse,
        profile: ModelProfile,
        turn: int,
        attempts: int,
        failures: Sequence[DomainEvent],
        latency_ms: int,
    ) -> Execution | None:
        usage = response.usage
        cost = profile.price.cost_of(usage)
        uncached = Usage(
            input_tokens=usage.input_tokens
            + usage.cache_read_input_tokens
            + usage.cache_creation_input_tokens,
            output_tokens=usage.output_tokens,
        )
        plan = self._plan
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
            latency_ms=latency_ms,
            cache_savings_micro_usd=profile.price.cost_of(uncached) - cost,
            context_tokens=plan.total_tokens,
            context_blocks=tuple(f"{b.key}@{b.version}" for b in plan.blocks),
            elided=tuple(e.key for e in plan.elisions),
            cache_breakpoints=plan.breakpoints,
        )
        if response.tool_uses:
            await self._commit(state, execution, [*failures, called])
            return None
        final = execution.complete(response.text, self._rt.clock.now())
        return await self._finish(final, [*failures, called], state=state)

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
            call.name,
            tools=self._rt.tools,
            version=self._version,
            task=self._task,
            mission=mission,
            arguments=call.arguments,
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
            loaded = set(loaded_tools(self._state))
            context = ToolContext(
                idempotency_key=key,
                execution_id=self._execution.id,
                mission_id=self._execution.mission_id,
                agent_id=self._execution.agent_id,
                discoverable=tuple(
                    tool
                    for tool in authorized_tools(self._rt, self._version, self._task, mission)
                    if tool.deferred and tool.name not in loaded
                ),
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
            arguments_sha256=arguments_digest(call),
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
                        arguments_sha256=arguments_digest(call),
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
            await budget.release(uow, execution.id)  # actual spend is now recorded
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

    async def _reserve(self, mission: Mission, worst_case: int) -> Execution | None:
        """Reserve the next call's worst-case cost against the shared mission budget (ADR 0006).

        If only other tasks' reservations stand in the way, wait for them to settle: their
        actual spend is usually below their worst case. Fail only when spend alone leaves
        no room, or the wait outlasts RESERVATION_WAIT_SECONDS.
        """
        deadline = time.monotonic() + RESERVATION_WAIT_SECONDS
        while True:
            async with self._rt.uow_factory() as uow:
                granted, committed, limit = await budget.reserve(
                    uow, mission.id, self._execution.id, worst_case, self._rt.clock.now()
                )
                spent = await uow.executions.mission_spend(mission.id)
            if granted:
                return None
            if spent + worst_case > limit or time.monotonic() > deadline:
                return await self._fail(
                    FailureKind.BUDGET_EXCEEDED,
                    f"next call may cost {worst_case} micro-USD; {committed} of {limit} "
                    "already spent or reserved",
                )
            await asyncio.sleep(RESERVATION_POLL_SECONDS)

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
            task = await uow.tasks.get(final.task_id)
            superseded = mission.status.is_terminal or task.status is TaskStatus.CANCELLED
            if superseded and final.status is not ExecutionStatus.CANCELLED:
                final = self._execution.cancel(now)
            await uow.executions.update(final, fence=self._fence, lease_seconds=0)
            await budget.release(uow, final.id)
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


def arguments_digest(call: ToolUseBlock) -> str:
    """Audit fingerprint of a call's arguments; the arguments themselves stay in the checkpoint."""
    canonical = json.dumps(dict(call.arguments), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _started(execution: Execution, worker_id: str) -> AgentStarted:
    return AgentStarted(
        aggregate_id=execution.id,
        mission_id=execution.mission_id,
        attempt=execution.attempt,
        worker_id=worker_id,
        agent_id=execution.agent_id,
        agent_version_id=execution.agent_version_id,
    )


def _routed(execution: Execution, decision: RoutingDecision, required_tokens: int) -> ModelRouted:
    return ModelRouted(
        aggregate_id=execution.id,
        mission_id=execution.mission_id,
        selected=None
        if decision.selected is None
        else f"{decision.selected.provider}/{decision.selected.name}",
        candidates=tuple(
            f"{c.model.provider}/{c.model.name}: {c.reason}" for c in decision.candidates
        ),
        required_context_tokens=required_tokens,
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
