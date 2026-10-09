"""Tool registry endpoints: inspect tools and pin a reviewed version."""

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from forge.api.dependencies import clock, operator, uow_factory
from forge.domain.tool import RegisteredTool
from forge.tools import registry

router = APIRouter(prefix="/tools", tags=["tools"])


class ToolView(BaseModel):
    name: str
    pinned_version: str
    pinned_sha256: str
    status: str
    reason: str
    updated_at: datetime

    @classmethod
    def of(cls, tool: RegisteredTool) -> "ToolView":
        return cls(
            name=tool.name,
            pinned_version=tool.pinned_version,
            pinned_sha256=tool.pinned_sha256,
            status=tool.status.value,
            reason=tool.reason,
            updated_at=tool.updated_at,
        )


class PinRequest(BaseModel):
    version: str = Field(min_length=1, max_length=100)


@router.get("")
async def list_tools(request: Request) -> list[ToolView]:
    async with uow_factory(request)() as uow:
        return [ToolView.of(tool) for tool in await uow.tool_registry.list()]


@router.post("/{name}/pin")
async def pin_tool(
    name: str, body: PinRequest, request: Request, by: str = Depends(operator)
) -> ToolView:
    """Operator approval: make this exact registered version the executable one."""
    pinned = await registry.pin(
        uow_factory(request), clock(request), name, body.version, operator=by
    )
    return ToolView.of(pinned)
