"""Secrets, argument validation, registry rules, and deferred-loading derivation."""

import json
from dataclasses import replace

import pytest

from forge.domain.checkpoint import AgentState
from forge.domain.cost import Usage
from forge.domain.mission import RiskPolicy
from forge.domain.model import ModelResponse, ToolResultBlock, ToolUseBlock
from forge.domain.risk import RiskLevel
from forge.domain.tool import (
    RegisteredTool,
    ToolSource,
    ToolStatus,
    ToolVersionRecord,
    definition_sha256,
)
from forge.security.secrets import EnvSecretResolver, SecretUnavailableError, redact
from forge.tools.base import ToolContext
from forge.tools.pipeline import Stage, authorize
from forge.tools.registry import quarantine_reason
from forge.tools.search import ToolSearchTool, loaded_tools
from forge.tools.validation import argument_errors, schema_problem
from tests import factories
from tests.factories import NOW

pytestmark = pytest.mark.anyio

SCHEMA = {
    "type": "object",
    "properties": {"service": {"type": "string"}},
    "required": ["service"],
    "additionalProperties": False,
}


def test_secrets_resolve_from_the_environment_and_never_echo_values() -> None:
    resolver = EnvSecretResolver({"FORGE_SECRET_STATUS_API": "s3cr3t"})

    assert resolver.resolve("status_api") == "s3cr3t"
    with pytest.raises(SecretUnavailableError) as excinfo:
        resolver.resolve("missing")
    assert "s3cr3t" not in str(excinfo.value)
    assert redact("token=s3cr3t; again s3cr3t", ("s3cr3t",)) == "token=[REDACTED]; again [REDACTED]"


def test_arguments_are_validated_against_the_schema() -> None:
    definition = factories.tool_definition(input_schema=SCHEMA)

    assert argument_errors(definition, {"service": "checkout"}) == []
    assert argument_errors(definition, {}) == ["<root>: 'service' is a required property"]
    assert argument_errors(definition, {"service": "x", "rm": "-rf"})[0].startswith("<root>")
    assert schema_problem(factories.tool_definition(input_schema={"type": "nope"})) is not None
    assert schema_problem(factories.tool_definition(input_schema={"type": "string"})) is not None


def test_validation_runs_after_permission_and_before_risk() -> None:
    agent, version = factories.agent_and_version()
    mission = factories.mission(risk_policy=RiskPolicy(RiskLevel.LOW))
    task = factories.task(mission_id=mission.id, agent_id=agent.id)

    class Tool:
        definition = factories.tool_definition(input_schema=SCHEMA, risk=RiskLevel.HIGH)

        async def invoke(self, *_: object) -> None: ...

    tools = {"metrics.read": Tool()}

    def stage(**kwargs: object) -> Stage | None:
        args = {"tools": tools, "version": version, "task": task, "mission": mission, **kwargs}
        return authorize("metrics.read", **args).stage  # type: ignore[arg-type]

    assert stage(arguments={}) is Stage.VALIDATION
    assert stage(arguments={"service": "checkout"}) is Stage.RISK
    assert stage(task=replace(task, allowed_tools=()), arguments={}) is Stage.PERMISSION


def _record(definition_hash: str, version: str = "1") -> ToolVersionRecord:
    return ToolVersionRecord(
        "metrics.read", version, ToolSource.NATIVE, "native", definition_hash, NOW
    )


def _pinned(version: str, sha: str, status: ToolStatus = ToolStatus.ACTIVE) -> RegisteredTool:
    return RegisteredTool("metrics.read", version, sha, status, "", NOW)


def test_registry_quarantine_rules() -> None:
    native = factories.tool_definition()
    sha = definition_sha256(native)
    mcp = replace(native, source=ToolSource.MCP, version="sha-new")

    assert quarantine_reason(native, sha, None, None) is None  # first sighting
    assert quarantine_reason(native, sha, _record(sha), _pinned("1", sha)) is None  # unchanged
    assert "without a version bump" in str(
        quarantine_reason(native, sha, _record("other"), _pinned("1", "other"))
    )
    native_v2 = replace(native, version="2")
    assert (
        quarantine_reason(native_v2, definition_sha256(native_v2), None, _pinned("1", sha)) is None
    )
    assert "operator must pin" in str(
        quarantine_reason(mcp, definition_sha256(mcp), None, _pinned("sha-old", sha))
    )
    assert (
        quarantine_reason(native, sha, None, _pinned("1", "x", ToolStatus.QUARANTINED)) is not None
    )
    assert quarantine_reason(native, sha, None, _pinned("1", sha, ToolStatus.QUARANTINED)) is None


async def test_search_returns_only_discoverable_tools_and_the_load_is_derivable() -> None:
    logs = factories.tool_definition(
        name="logs.read", description="Read service logs", deferred=True
    )
    context = ToolContext("k", NOW_ID, NOW_ID, NOW_ID, discoverable=(logs,))

    result = await ToolSearchTool().invoke({"query": "logs"}, context)

    assert json.loads(result.content)["loaded"] == ["logs.read"]
    state = AgentState.initial("o").with_model_response(
        ModelResponse((ToolUseBlock("s1", "tools.search", {"query": "logs"}),), "tool_use", Usage())
    )
    state = state.with_tool_result(ToolResultBlock("s1", result.content))
    assert loaded_tools(state) == ("logs.read",)
    empty = await ToolSearchTool().invoke({"query": "deploy"}, context)
    assert json.loads(empty.content)["loaded"] == []


def test_failed_or_malformed_search_results_load_nothing() -> None:
    state = AgentState.initial("o").with_model_response(
        ModelResponse(
            (ToolUseBlock("s1", "tools.search", {}), ToolUseBlock("s2", "tools.search", {})),
            "tool_use",
            Usage(),
        )
    )
    state = state.with_tool_result(ToolResultBlock("s1", '{"loaded": ["x"]}', is_error=True))
    state = state.with_tool_result(ToolResultBlock("s2", "not json"))

    assert loaded_tools(state) == ()


NOW_ID = factories.mission().id
