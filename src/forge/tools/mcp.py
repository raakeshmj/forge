"""MCP adapter: MCP is a transport; FORGE tools are FORGE-owned definitions (ADR 0005).

Nothing outside this module imports the MCP SDK. FORGE takes only the input schema
(and, unless configured, a truncated description) from a server. Risk, idempotency,
timeouts, and which tools exist at all come from operator configuration.
"""

import hashlib
import json
import logging
import os
from collections.abc import Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

import anyio
import mcp.types
from mcp import Client, MCPError, StdioServerParameters

from forge.domain.common import JSONValue
from forge.domain.risk import RiskLevel
from forge.domain.tool import Idempotency, ToolDefinition, ToolResult, ToolSource
from forge.security import egress
from forge.tools.base import ToolContext, ToolExecutionError
from forge.tools.registry import Discovered

_log = logging.getLogger(__name__)

MAX_OUTPUT_BYTES = 32 * 1024
MAX_DESCRIPTION_CHARS = 1024
_TRANSPORT_ERRORS = (
    MCPError,
    OSError,
    anyio.BrokenResourceError,
    anyio.ClosedResourceError,
    anyio.EndOfStream,
)


@dataclass(frozen=True, slots=True)
class McpToolConfig:
    remote_name: str  # the server's name for the tool
    name: str  # the FORGE name agents bind to
    risk: RiskLevel
    idempotency: Idempotency
    timeout_seconds: float = 10.0
    max_attempts: int = 1
    deferred: bool = False
    description: str | None = None  # operator-written; preferred over the server's


@dataclass(frozen=True, slots=True)
class McpServerConfig:
    id: str
    tools: tuple[McpToolConfig, ...]
    command: tuple[str, ...] = ()  # stdio transport
    url: str | None = None  # streamable HTTP transport
    env: Mapping[str, str] = field(default_factory=dict)  # the only variables a stdio server sees
    egress_policy: egress.EgressPolicy | None = None  # required for `url`


class McpConnection:
    """One live MCP client session, owned by the worker for its lifetime."""

    def __init__(self, server_id: str, client: Client) -> None:
        self.server_id = server_id
        self._client = client

    @classmethod
    async def open(cls, config: McpServerConfig, stack: AsyncExitStack) -> "McpConnection":
        target: Any
        if config.command:
            # Minimal environment: FORGE's own variables (database URL, provider keys,
            # FORGE_SECRET_*) never reach the server process.
            env = {"PATH": os.environ.get("PATH", ""), **config.env}
            target = StdioServerParameters(
                command=config.command[0], args=list(config.command[1:]), env=env
            )
        elif config.url is not None:
            if config.egress_policy is None:
                raise ToolExecutionError(
                    f"MCP server {config.id}: url requires an egress policy", retryable=False
                )
            await egress.check(config.url, config.egress_policy)
            target = config.url
        else:
            raise ToolExecutionError(
                f"MCP server {config.id}: no transport configured", retryable=False
            )
        client = await stack.enter_async_context(Client(target))
        return cls(config.id, client)

    @classmethod
    async def attach(cls, server_id: str, server: Any, stack: AsyncExitStack) -> "McpConnection":
        """Connect to an in-process server object (used by tests and embedded servers)."""
        return cls(server_id, await stack.enter_async_context(Client(server)))

    async def list_tools(self) -> list[mcp.types.Tool]:
        return list((await self._client.list_tools()).tools)

    async def call(self, name: str, arguments: dict[str, Any]) -> Any:
        return await self._client.call_tool(name, arguments)


class McpTool:
    def __init__(
        self, connection: McpConnection, remote: mcp.types.Tool, config: McpToolConfig
    ) -> None:
        self._connection = connection
        self._remote_name = remote.name
        description = config.description or (remote.description or "")[:MAX_DESCRIPTION_CHARS]
        schema: dict[str, JSONValue] = dict(remote.input_schema)
        fingerprint = hashlib.sha256(
            json.dumps({"schema": schema, "description": description}, sort_keys=True).encode()
        ).hexdigest()
        self._definition = ToolDefinition(
            name=config.name,
            version=f"sha-{fingerprint[:12]}",  # any server-side change is a new version
            description=description,
            input_schema=schema,
            risk=config.risk,
            idempotency=config.idempotency,
            timeout_seconds=config.timeout_seconds,
            max_attempts=config.max_attempts,
            source=ToolSource.MCP,
            deferred=config.deferred,
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def invoke(self, arguments: Mapping[str, JSONValue], context: ToolContext) -> ToolResult:
        try:
            result = await self._connection.call(self._remote_name, dict(arguments))
        except _TRANSPORT_ERRORS as exc:
            raise ToolExecutionError(
                f"MCP server {self._connection.server_id} failed: {type(exc).__name__}",
                retryable=True,
            ) from exc
        if not isinstance(result, mcp.types.CallToolResult):
            return ToolResult(
                "The tool asked for interactive input, which is not supported.", is_error=True
            )
        return normalize(result)


def normalize(result: mcp.types.CallToolResult) -> ToolResult:
    """Text only, size-capped, error flag preserved. MCP content is untrusted data."""
    texts = [item.text for item in result.content if isinstance(item, mcp.types.TextContent)]
    omitted = len(result.content) - len(texts)
    text = "\n".join(texts)
    if omitted:
        text += f"\n[{omitted} non-text content item(s) omitted]"
    if len(text.encode()) > MAX_OUTPUT_BYTES:
        return ToolResult(
            f"Tool output exceeded {MAX_OUTPUT_BYTES} bytes and was discarded.", is_error=True
        )
    return ToolResult(text, is_error=bool(result.is_error))


async def discover(connection: McpConnection, config: McpServerConfig) -> list[Discovered]:
    """Normalize the allowlisted tools a server offers. Unlisted tools are ignored."""
    offered = {tool.name: tool for tool in await connection.list_tools()}
    discovered = []
    for tool_config in config.tools:
        remote = offered.get(tool_config.remote_name)
        if remote is None:
            _log.warning(
                "configured MCP tool not offered by server",
                extra={"server": config.id, "tool": tool_config.remote_name},
            )
            continue
        discovered.append(
            Discovered(
                McpTool(connection, remote, tool_config),
                source_ref=f"mcp:{config.id}/{tool_config.remote_name}",
            )
        )
    return discovered
