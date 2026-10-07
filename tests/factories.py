"""Builders for domain objects with valid defaults. Override only what a test is about."""

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from forge.domain.agent import Agent, AgentVersion, ModelRef
from forge.domain.mission import Budget, ExecutionPolicy, Mission, MissionStatus, RiskPolicy
from forge.domain.risk import RiskLevel
from forge.domain.task import Task, TaskStatus
from forge.domain.tool import Idempotency, ToolDefinition

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def mission(**overrides: Any) -> Mission:
    base = Mission(
        id=uuid4(),
        version=1,
        objective="Investigate checkout latency",
        constraints=(),
        success_criteria=(),
        required_capabilities=(),
        budget=Budget(1_000_000),
        deadline=None,
        risk_policy=RiskPolicy(),
        execution_policy=ExecutionPolicy(),
        status=MissionStatus.CREATED,
        correlation_id="corr-1",
        created_at=NOW,
        updated_at=NOW,
    )
    return replace(base, **overrides)


def task(**overrides: Any) -> Task:
    base = Task(
        id=uuid4(),
        version=1,
        mission_id=uuid4(),
        objective="Investigate checkout latency",
        agent_id=uuid4(),
        allowed_tools=("metrics.read",),
        status=TaskStatus.PENDING,
        created_at=NOW,
        updated_at=NOW,
    )
    return replace(base, **overrides)


def agent_and_version(**version_overrides: Any) -> tuple[Agent, AgentVersion]:
    agent = Agent(id=uuid4(), name=f"agent-{uuid4().hex[:8]}", current_version=1, created_at=NOW)
    version = AgentVersion(
        id=uuid4(),
        agent_id=agent.id,
        version=1,
        role="investigator",
        instructions="Investigate using the tools available.",
        model=ModelRef("demo", "demo-investigator-1"),
        tools=("metrics.read",),
        created_at=NOW,
    )
    return agent, replace(version, **version_overrides)


def tool_definition(**overrides: Any) -> ToolDefinition:
    base = ToolDefinition(
        name="metrics.read",
        version="1",
        description="Read metrics",
        input_schema={"type": "object"},
        risk=RiskLevel.LOW,
        idempotency=Idempotency.READ_ONLY,
        timeout_seconds=5.0,
    )
    return replace(base, **overrides)
