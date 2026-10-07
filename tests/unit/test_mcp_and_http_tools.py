"""MCP normalization and remote HTTP tool safety, without real servers or networks."""

import json
from contextlib import AsyncExitStack

import httpx2
import mcp.types
import pytest
from mcp.server.mcpserver import MCPServer

from forge.domain.risk import RiskLevel
from forge.domain.tool import Idempotency, ToolSource
from forge.security.egress import EgressPolicy, allowlist
from forge.security.secrets import EnvSecretResolver
from forge.tools.base import ToolContext, ToolExecutionError
from forge.tools.http_api import MAX_RESPONSE_BYTES, HttpApiTool, HttpToolConfig
from forge.tools.mcp import (
    MAX_OUTPUT_BYTES,
    McpConnection,
    McpServerConfig,
    McpToolConfig,
    discover,
    normalize,
)
from tests import factories

pytestmark = pytest.mark.anyio
CONTEXT = ToolContext("k", factories.mission().id, factories.mission().id, factories.mission().id)


def server() -> MCPServer:
    s = MCPServer("observability")

    @s.tool(description="Latency for a service. " + "x" * 5000)
    def query_latency(service: str) -> str:
        return f"{service}: p95=880ms"

    @s.tool(description="Not allowlisted")
    def delete_everything() -> str:
        return "gone"

    return s


CONFIG = McpServerConfig(
    id="observability",
    tools=(
        McpToolConfig("query_latency", "metrics.latency", RiskLevel.LOW, Idempotency.READ_ONLY),
        McpToolConfig("not_offered", "metrics.other", RiskLevel.LOW, Idempotency.READ_ONLY),
    ),
)


async def test_only_allowlisted_tools_are_discovered_with_forge_owned_classification() -> None:
    async with AsyncExitStack() as stack:
        connection = await McpConnection.attach("observability", server(), stack)
        (item,) = await discover(connection, CONFIG)
        definition = item.tool.definition

        result = await item.tool.invoke({"service": "checkout"}, CONTEXT)

    assert definition.name == "metrics.latency"
    assert definition.source is ToolSource.MCP
    assert definition.risk is RiskLevel.LOW  # from configuration, never from the server
    assert definition.version.startswith("sha-")
    assert len(definition.description) == 1024  # server text is truncated
    assert definition.input_schema["required"] == ["service"]
    assert item.source_ref == "mcp:observability/query_latency"
    assert result.content == "checkout: p95=880ms"


async def test_a_changed_server_schema_is_a_new_version() -> None:
    async with AsyncExitStack() as stack:
        before = (await discover(await McpConnection.attach("o", server(), stack), CONFIG))[0]
        changed = MCPServer("observability")

        @changed.tool(description="Latency for a service. " + "x" * 5000)
        def query_latency(service: str, window: str) -> str:
            return "x"

        after = (await discover(await McpConnection.attach("o", changed, stack), CONFIG))[0]

    assert before.tool.definition.version != after.tool.definition.version


def test_results_are_text_only_size_capped_and_keep_the_error_flag() -> None:
    image = mcp.types.ImageContent(type="image", data="aGk=", mime_type="image/png")
    mixed = mcp.types.CallToolResult(content=[mcp.types.TextContent(type="text", text="ok"), image])
    huge = mcp.types.CallToolResult(
        content=[mcp.types.TextContent(type="text", text="x" * (MAX_OUTPUT_BYTES + 1))]
    )
    failed = mcp.types.CallToolResult(
        content=[mcp.types.TextContent(type="text", text="no")], is_error=True
    )

    assert normalize(mixed).content == "ok\n[1 non-text content item(s) omitted]"
    assert normalize(huge).is_error
    assert normalize(failed).is_error


async def test_transport_failure_is_retryable() -> None:
    class Broken:
        server_id = "x"

        async def call(self, name: str, arguments: dict[str, object]) -> object:
            raise OSError("pipe closed")

        async def list_tools(self) -> list[mcp.types.Tool]:
            return [mcp.types.Tool(name="query_latency", input_schema={"type": "object"})]

    (item,) = await discover(Broken(), CONFIG)  # type: ignore[arg-type]
    with pytest.raises(ToolExecutionError) as excinfo:
        await item.tool.invoke({}, CONTEXT)
    assert excinfo.value.retryable


# --- Remote HTTP tool --------------------------------------------------------------------

SECRET = "tok-abc123"  # noqa: S105 - test credential


def http_tool(handler, address: str = "93.184.216.34") -> HttpApiTool:  # type: ignore[no-untyped-def]
    async def resolve(host: str, port: int) -> list[str]:
        return [address]

    config = HttpToolConfig(
        name="status.incidents",
        description="Open incidents",
        url="https://status.example.com/api/incidents",
        query_parameters=("service",),
        policy=EgressPolicy(allowlist(["status.example.com"])),
        credential="status_api",
    )
    return HttpApiTool(
        config,
        EnvSecretResolver({"FORGE_SECRET_STATUS_API": SECRET}),
        resolver=resolve,
        transport=httpx2.MockTransport(handler),
    )


async def test_request_goes_to_the_vetted_address_with_credential_and_secret_is_redacted() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        echoed = request.headers["Authorization"]
        return httpx2.Response(200, text=json.dumps({"incidents": 0, "echo": echoed}))

    result = await http_tool(handler).invoke({"service": "checkout"}, CONTEXT)

    (request,) = seen
    assert str(request.url) == "https://93.184.216.34/api/incidents?service=checkout"
    assert request.headers["Host"] == "status.example.com"
    assert request.extensions["sni_hostname"] == "status.example.com"
    assert request.headers["Authorization"] == f"Bearer {SECRET}"
    assert SECRET not in result.content
    assert "[REDACTED]" in result.content
    assert not result.is_error


@pytest.mark.parametrize(
    ("response", "expectation"),
    [
        (httpx2.Response(302, headers={"Location": "http://169.254.169.254/"}), "redirect"),
        (httpx2.Response(200, text="x" * (MAX_RESPONSE_BYTES + 1)), "exceeded"),
        (httpx2.Response(404, text="nope"), "nope"),
    ],
)
async def test_redirects_oversize_and_client_errors_become_error_results(
    response: httpx2.Response, expectation: str
) -> None:
    result = await http_tool(lambda _: response).invoke({}, CONTEXT)

    assert result.is_error
    assert expectation in result.content


async def test_server_errors_are_retryable_and_internal_targets_never_contacted() -> None:
    calls: list[httpx2.Request] = []

    with pytest.raises(ToolExecutionError) as excinfo:
        await http_tool(lambda r: httpx2.Response(503)).invoke({}, CONTEXT)
    blocked = await http_tool(lambda r: calls.append(r) or httpx2.Response(200), "10.0.0.5").invoke(  # type: ignore[func-returns-value]
        {}, CONTEXT
    )

    assert excinfo.value.retryable
    assert blocked.is_error
    assert "egress" in blocked.content
    assert calls == []
