"""Agent registration endpoints."""

from fastapi import APIRouter, Request, status

from forge.api.dependencies import clock, uow_factory
from forge.api.schemas import AgentCreate, AgentCreated
from forge.domain.agent import ModelRef
from forge.observability.logs import correlation_id
from forge.orchestration.agents import register_agent

router = APIRouter(prefix="/agents", tags=["agents"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_agent(body: AgentCreate, request: Request) -> AgentCreated:
    version = await register_agent(
        uow_factory(request),
        clock(request),
        name=body.name,
        role=body.role,
        instructions=body.instructions,
        model=ModelRef(body.model.provider, body.model.name),
        fallback_models=[ModelRef(m.provider, m.name) for m in body.fallback_models],
        model_options=body.model_options,
        tools=body.tools,
        capabilities=body.capabilities,
        correlation_id=correlation_id.get(),
    )
    return AgentCreated(agent_id=version.agent_id, version=version.version, version_id=version.id)
