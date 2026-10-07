"""Agent registry: registers agents with their first immutable version."""

from collections.abc import Sequence
from uuid import uuid4

from forge.domain.agent import Agent, AgentVersion, ModelRef
from forge.domain.events import AgentRegistered
from forge.domain.repositories import Clock, UnitOfWorkFactory


async def register_agent(
    uow_factory: UnitOfWorkFactory,
    clock: Clock,
    *,
    name: str,
    role: str,
    instructions: str,
    model: ModelRef,
    tools: Sequence[str],
    correlation_id: str | None,
) -> AgentVersion:
    now = clock.now()
    agent = Agent(id=uuid4(), name=name, current_version=1, created_at=now)
    version = AgentVersion(
        id=uuid4(),
        agent_id=agent.id,
        version=1,
        role=role,
        instructions=instructions,
        model=model,
        tools=tuple(sorted(set(tools))),
        created_at=now,
    )
    async with uow_factory() as uow:
        await uow.agents.add(agent, version)
        await uow.events.append(
            AgentRegistered(
                aggregate_id=agent.id,
                mission_id=None,
                name=agent.name,
                version=version.version,
                model_provider=model.provider,
                model_name=model.name,
                tools=version.tools,
            ),
            correlation_id=correlation_id,
        )
    return version
