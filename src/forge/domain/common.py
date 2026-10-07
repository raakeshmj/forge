"""Shared domain primitives: JSON values, errors, and explicit state machines."""

from collections.abc import Mapping
from enum import StrEnum
from uuid import UUID

type JSONValue = bool | int | float | str | list[JSONValue] | dict[str, JSONValue] | None
type JSONObject = dict[str, JSONValue]


class DomainError(Exception):
    """A domain rule was violated. Never retryable: retrying cannot change the outcome."""


class InvalidTransitionError(DomainError):
    def __init__(self, entity: str, entity_id: UUID, current: StrEnum, target: StrEnum) -> None:
        super().__init__(f"{entity} {entity_id}: cannot transition {current} -> {target}")
        self.entity = entity
        self.entity_id = entity_id
        self.current = current
        self.target = target


def require_transition[S: StrEnum](
    transitions: Mapping[S, frozenset[S]], entity: str, entity_id: UUID, current: S, target: S
) -> None:
    """Raise unless `current -> target` is an edge of the state machine."""
    if target not in transitions.get(current, frozenset()):
        raise InvalidTransitionError(entity, entity_id, current, target)
