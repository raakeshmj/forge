"""Reconstruct what an execution did from its checkpoints and events (read-only)."""

import json
from dataclasses import dataclass
from uuid import UUID

from forge.domain.checkpoint import AgentState
from forge.domain.cost import Usage
from forge.domain.events import EventRecord
from forge.domain.execution import Execution, ExecutionStatus
from forge.domain.mission import Mission
from forge.domain.model import Role, ToolResultBlock, ToolUseBlock
from forge.domain.repositories import UnitOfWork
from forge.domain.task import Task, TaskKind


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    tool_use_id: str
    tool: str
    arguments_digest: str  # canonical arguments, for duplicate detection
    arguments_text: str
    turn: int
    status: str  # completed | failed | denied | pending
    denial_stage: str | None
    result: str | None
    is_error: bool


@dataclass(frozen=True, slots=True)
class Subject:
    """Everything an evaluator may look at, for one task or a whole mission."""

    output: str | None
    completed: bool
    tool_calls: tuple[ToolCallRecord, ...]
    turns: int
    max_steps: int
    model_attempts_failed: int
    usage: Usage
    cost_micro_usd: int
    budget_micro_usd: int
    models: tuple[str, ...]  # provider/name used


@dataclass(frozen=True, slots=True)
class TaskTrajectory:
    task: Task
    execution: Execution | None  # the latest attempt
    subject: Subject


@dataclass(frozen=True, slots=True)
class MissionTrajectory:
    mission: Mission
    tasks: tuple[TaskTrajectory, ...]  # WORK tasks only
    subject: Subject  # the mission as a whole


async def load(uow: UnitOfWork, mission_id: UUID) -> MissionTrajectory:
    mission = await uow.missions.get(mission_id)
    events = await uow.events.list_for_mission(mission_id)
    tasks = [t for t in await uow.tasks.list_for_mission(mission_id) if t.kind is TaskKind.WORK]
    trajectories = []
    for task in tasks:
        execution = await uow.executions.latest_for_task(task.id)
        state = None
        if execution is not None:
            checkpoint = await uow.checkpoints.latest(execution.id)
            state = checkpoint.state if checkpoint else None
        trajectories.append(
            TaskTrajectory(
                task, execution, _subject(mission, [execution] if execution else [], state, events)
            )
        )
    sinks = [
        t
        for t in trajectories
        if not any(t.task.id in other.task.dependencies for other in trajectories)
    ]
    outputs = [t.subject.output for t in sinks if t.subject.output]
    executions = [t.execution for t in trajectories if t.execution is not None]
    combined_calls = tuple(call for t in trajectories for call in t.subject.tool_calls)
    whole = Subject(
        output="\n\n".join(outputs) if outputs else None,
        completed=all(t.subject.completed for t in trajectories) and bool(trajectories),
        tool_calls=combined_calls,
        turns=sum(t.subject.turns for t in trajectories),
        max_steps=sum(t.subject.max_steps for t in trajectories),
        model_attempts_failed=sum(t.subject.model_attempts_failed for t in trajectories),
        usage=sum((e.usage for e in executions), Usage()),
        cost_micro_usd=await uow.executions.mission_spend(mission_id),
        budget_micro_usd=mission.budget.limit_micro_usd,
        models=tuple(sorted({m for t in trajectories for m in t.subject.models})),
    )
    return MissionTrajectory(mission, tuple(trajectories), whole)


def _subject(
    mission: Mission,
    executions: list[Execution],
    state: AgentState | None,
    events: list[EventRecord],
) -> Subject:
    execution_ids = {e.id for e in executions}
    own = [e for e in events if e.aggregate_id in execution_ids]
    denied = {
        str(e.payload.get("tool_use_id")): str(e.payload.get("stage"))
        for e in own
        if e.event_type == "tool.denied"
    }
    failed = {str(e.payload.get("tool_use_id")) for e in own if e.event_type == "tool.failed"}
    calls = _tool_calls(state, denied, failed) if state else ()
    usage = sum((e.usage for e in executions), Usage())
    return Subject(
        output=executions[-1].output if executions else None,
        completed=bool(executions) and executions[-1].status is ExecutionStatus.COMPLETED,
        tool_calls=calls,
        turns=state.turns if state else 0,
        max_steps=mission.execution_policy.max_steps,
        model_attempts_failed=sum(1 for e in own if e.event_type == "model.failed"),
        usage=usage,
        cost_micro_usd=sum(e.cost_micro_usd for e in executions),
        budget_micro_usd=mission.budget.limit_micro_usd,
        models=tuple(sorted({f"{e.model.provider}/{e.model.name}" for e in executions})),
    )


def _tool_calls(
    state: AgentState, denied: dict[str, str], failed: set[str]
) -> tuple[ToolCallRecord, ...]:
    results = {
        block.tool_use_id: block
        for message in state.messages
        for block in message.content
        if isinstance(block, ToolResultBlock)
    }
    records = []
    turn = -1
    for message in state.messages:
        if message.role is Role.ASSISTANT:
            turn += 1
        for block in message.content:
            if not isinstance(block, ToolUseBlock):
                continue
            result = results.get(block.id)
            if block.id in denied:
                status = "denied"
            elif result is None:
                status = "pending"
            elif block.id in failed:
                status = "failed"
            else:
                status = "completed"
            arguments = json.dumps(dict(block.arguments), sort_keys=True)
            records.append(
                ToolCallRecord(
                    tool_use_id=block.id,
                    tool=block.name,
                    arguments_digest=f"{block.name}:{arguments}",
                    arguments_text=arguments,
                    turn=turn,
                    status=status,
                    denial_stage=denied.get(block.id),
                    result=None if result is None else result.content,
                    is_error=bool(result and result.is_error),
                )
            )
    return tuple(records)
