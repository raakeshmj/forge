"""The tool port."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from forge.domain.common import JSONValue
from forge.domain.tool import ToolDefinition, ToolResult


class ToolExecutionError(Exception):
    """The tool ran and definitely failed. `retryable` marks transient faults.

    A timeout or crash is different: the outcome is unknown (ADR 0002).
    """

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class ToolContext:
    """What a tool may know about its caller. Credentials are resolved by the tool itself."""

    idempotency_key: str
    execution_id: UUID
    mission_id: UUID
    agent_id: UUID
    # Deferred tools this agent may load; only `tools.search` reads it.
    discoverable: tuple[ToolDefinition, ...] = ()


class Tool(Protocol):
    @property
    def definition(self) -> ToolDefinition: ...

    async def invoke(self, arguments: Mapping[str, JSONValue], context: ToolContext) -> ToolResult:
        """Validate `arguments` itself (they come from the model); raise ToolExecutionError."""
        ...
