"""Tool Registry: the single writer of tool versions and their status (ADR 0005).

Workers sync the tools they can execute at startup. Only ACTIVE tools whose
definition matches the pinned fingerprint are executable; anything that changed
behind FORGE's back is quarantined until an operator pins it.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from forge.domain.common import DomainError
from forge.domain.events import ToolPinned, ToolQuarantined, ToolRegistered
from forge.domain.repositories import Clock, NotFoundError, UnitOfWork, UnitOfWorkFactory
from forge.domain.tool import (
    RegisteredTool,
    ToolDefinition,
    ToolSource,
    ToolStatus,
    ToolVersionRecord,
    definition_sha256,
    tool_aggregate_id,
)
from forge.tools.base import Tool
from forge.tools.validation import schema_problem

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Discovered:
    tool: Tool
    source_ref: str


@dataclass(frozen=True, slots=True)
class SyncResult:
    active: dict[str, Tool]
    quarantined: dict[str, str]  # name -> reason


async def sync(
    uow_factory: UnitOfWorkFactory, clock: Clock, discovered: Sequence[Discovered]
) -> SyncResult:
    active: dict[str, Tool] = {}
    quarantined: dict[str, str] = {}
    names = [item.tool.definition.name for item in discovered]
    if len(names) != len(set(names)):
        raise DomainError(f"duplicate tool names discovered: {sorted(names)}")
    for item in discovered:
        async with uow_factory() as uow:
            reason = await _sync_one(uow, clock, item)
        if reason is None:
            active[item.tool.definition.name] = item.tool
        else:
            quarantined[item.tool.definition.name] = reason
            _log.warning(
                "tool quarantined",
                extra={"tool": item.tool.definition.name, "reason": reason},
            )
    return SyncResult(active, quarantined)


async def pin(
    uow_factory: UnitOfWorkFactory, clock: Clock, name: str, version: str
) -> RegisteredTool:
    """Operator approval of a (possibly changed) tool version."""
    async with uow_factory() as uow:
        record = await uow.tool_registry.get_version(name, version)
        if record is None:
            raise NotFoundError("tool version", f"{name}@{version}")
        tool = RegisteredTool(
            name, version, record.definition_sha256, ToolStatus.ACTIVE, "", clock.now()
        )
        await uow.tool_registry.save(tool)
        await uow.events.append(
            ToolPinned(
                aggregate_id=tool_aggregate_id(name),
                mission_id=None,
                name=name,
                version=version,
                definition_sha256=record.definition_sha256,
            ),
            correlation_id=None,
        )
    return tool


async def _sync_one(uow: UnitOfWork, clock: Clock, item: Discovered) -> str | None:
    """Register or check one tool. Returns a quarantine reason, or None if it is usable."""
    definition = item.tool.definition
    fingerprint = definition_sha256(definition)
    now = clock.now()
    existing = await uow.tool_registry.get(definition.name, for_update=True)
    known = await uow.tool_registry.get_version(definition.name, definition.version)
    if known is None:
        await uow.tool_registry.add_version(
            ToolVersionRecord(
                definition.name,
                definition.version,
                definition.source,
                item.source_ref,
                fingerprint,
                now,
            )
        )
        await uow.events.append(
            ToolRegistered(
                aggregate_id=tool_aggregate_id(definition.name),
                mission_id=None,
                name=definition.name,
                version=definition.version,
                source=definition.source.value,
                source_ref=item.source_ref,
                definition_sha256=fingerprint,
            ),
            correlation_id=None,
        )

    problem = quarantine_reason(definition, fingerprint, known, existing)
    if problem is not None:
        pinned_version = existing.pinned_version if existing else definition.version
        pinned_sha = existing.pinned_sha256 if existing else fingerprint
        await uow.tool_registry.save(
            RegisteredTool(
                definition.name, pinned_version, pinned_sha, ToolStatus.QUARANTINED, problem, now
            )
        )
        await uow.events.append(
            ToolQuarantined(
                aggregate_id=tool_aggregate_id(definition.name),
                mission_id=None,
                name=definition.name,
                pinned_version=pinned_version,
                observed_version=definition.version,
                observed_sha256=fingerprint,
                reason=problem,
            ),
            correlation_id=None,
        )
        return problem

    if existing is None or existing.pinned_version != definition.version:
        await uow.tool_registry.save(
            RegisteredTool(
                definition.name, definition.version, fingerprint, ToolStatus.ACTIVE, "", now
            )
        )
    return None


def quarantine_reason(
    definition: ToolDefinition,
    fingerprint: str,
    known: ToolVersionRecord | None,
    existing: RegisteredTool | None,
) -> str | None:
    """Why a discovered tool must not be executable, or None. Pure, so every rule is testable."""
    problem = schema_problem(definition)
    if problem is not None:
        return problem
    if known is not None and known.definition_sha256 != fingerprint:
        return f"definition of version {definition.version} changed without a version bump"
    if existing is None:
        return None
    if existing.pinned_version != definition.version:
        if definition.source is ToolSource.NATIVE:
            return None  # deployed code is operator-controlled: a new version advances the pin
        return (
            f"{definition.source.value} tool changed from pinned version "
            f"{existing.pinned_version}; an operator must pin {definition.version}"
        )
    if existing.status is ToolStatus.QUARANTINED and existing.pinned_sha256 != fingerprint:
        return existing.reason or "quarantined"
    return None
