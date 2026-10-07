"""`tools.search`: provider-neutral deferred tool loading (CONTEXT_ENGINEERING §6, ADR 0005).

Returns deferred tools the calling agent is authorized to use that match the query,
and loads them for the rest of the execution. The result is JSON so the loaded set
can be re-derived from the transcript on resume.
"""

import json
from collections.abc import Mapping, Sequence

from forge.domain.checkpoint import AgentState
from forge.domain.common import JSONValue
from forge.domain.model import Role, ToolResultBlock, ToolUseBlock
from forge.domain.risk import RiskLevel
from forge.domain.tool import Idempotency, ToolDefinition, ToolResult
from forge.tools.base import ToolContext

NAME = "tools.search"
_MAX_RESULTS = 5


class ToolSearchTool:
    def __init__(self) -> None:
        self._definition = ToolDefinition(
            name=NAME,
            version="1",
            description="Find and load additional tools by describing what you need to do.",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 200}},
                "required": ["query"],
                "additionalProperties": False,
            },
            risk=RiskLevel.LOW,
            idempotency=Idempotency.READ_ONLY,
            timeout_seconds=5.0,
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def invoke(self, arguments: Mapping[str, JSONValue], context: ToolContext) -> ToolResult:
        query = str(arguments.get("query", "")).lower()
        terms = [term for term in query.split() if term]
        matches = [
            tool
            for tool in context.discoverable
            if any(term in f"{tool.name} {tool.description}".lower() for term in terms)
        ][:_MAX_RESULTS]
        return ToolResult(
            json.dumps(
                {
                    "loaded": [tool.name for tool in matches],
                    "tools": [{"name": t.name, "description": t.description} for t in matches],
                },
                sort_keys=True,
            )
        )


def loaded_tools(state: AgentState) -> tuple[str, ...]:
    """Deferred tools loaded so far, in load order, derived from the transcript."""
    search_calls = {
        block.id
        for message in state.messages
        if message.role is Role.ASSISTANT
        for block in message.content
        if isinstance(block, ToolUseBlock) and block.name == NAME
    }
    loaded: list[str] = []
    for message in state.messages:
        for block in message.content:
            if isinstance(block, ToolResultBlock) and block.tool_use_id in search_calls:
                for name in _parse_loaded(block):
                    if name not in loaded:
                        loaded.append(name)
    return tuple(loaded)


def _parse_loaded(block: ToolResultBlock) -> Sequence[str]:
    if block.is_error:
        return ()
    try:
        payload = json.loads(block.content)
    except json.JSONDecodeError:
        return ()
    names = payload.get("loaded") if isinstance(payload, dict) else None
    return [name for name in names if isinstance(name, str)] if isinstance(names, list) else ()
