"""Operator tool configuration: MCP servers and remote API tools (JSON file, ADR 0005).

{
  "mcp_servers": [{"id": "...", "command": ["..."], "env": {}, "tools": [
      {"remote_name": "...", "name": "...", "risk": "LOW", "idempotency": "READ_ONLY",
       "timeout_seconds": 10, "max_attempts": 1, "deferred": false, "description": "..."}]}],
  "http_tools": [{"name": "...", "description": "...", "url": "https://...",
      "query_parameters": ["..."], "allowed_hosts": ["..."], "credential": "status_api"}]
}
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from forge.domain.common import DomainError
from forge.domain.risk import RiskLevel
from forge.domain.tool import Idempotency
from forge.security.egress import EgressPolicy, allowlist
from forge.tools.http_api import HttpToolConfig
from forge.tools.mcp import McpServerConfig, McpToolConfig


@dataclass(frozen=True, slots=True)
class ToolConfig:
    mcp_servers: tuple[McpServerConfig, ...] = ()
    http_tools: tuple[HttpToolConfig, ...] = ()


def load(path: Path) -> ToolConfig:
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise DomainError(f"cannot read tool configuration {path}: {exc}") from exc
    return parse(raw)


def parse(raw: dict[str, Any]) -> ToolConfig:
    try:
        return ToolConfig(
            mcp_servers=tuple(_server(s) for s in raw.get("mcp_servers", [])),
            http_tools=tuple(_http(t) for t in raw.get("http_tools", [])),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DomainError(f"invalid tool configuration: {exc}") from exc


def _policy(item: dict[str, Any]) -> EgressPolicy:
    return EgressPolicy(
        allowed_hosts=allowlist(item["allowed_hosts"]),
        allow_private=bool(item.get("allow_private", False)),
        allow_http=bool(item.get("allow_http", False)),
    )


def _server(item: dict[str, Any]) -> McpServerConfig:
    return McpServerConfig(
        id=str(item["id"]),
        command=tuple(str(part) for part in item.get("command", [])),
        url=item.get("url"),
        env={str(k): str(v) for k, v in item.get("env", {}).items()},
        egress_policy=_policy(item) if "allowed_hosts" in item else None,
        tools=tuple(
            McpToolConfig(
                remote_name=str(tool["remote_name"]),
                name=str(tool["name"]),
                risk=RiskLevel(tool["risk"]),
                idempotency=Idempotency(tool["idempotency"]),
                timeout_seconds=float(tool.get("timeout_seconds", 10.0)),
                max_attempts=int(tool.get("max_attempts", 1)),
                deferred=bool(tool.get("deferred", False)),
                description=tool.get("description"),
            )
            for tool in item["tools"]
        ),
    )


def _http(item: dict[str, Any]) -> HttpToolConfig:
    return HttpToolConfig(
        name=str(item["name"]),
        description=str(item["description"]),
        url=str(item["url"]),
        query_parameters=tuple(str(p) for p in item.get("query_parameters", [])),
        policy=_policy(item),
        credential=item.get("credential"),
        timeout_seconds=float(item.get("timeout_seconds", 10.0)),
        max_attempts=int(item.get("max_attempts", 2)),
        risk=RiskLevel(item.get("risk", "LOW")),
    )
