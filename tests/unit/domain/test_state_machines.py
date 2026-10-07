from collections.abc import Callable
from enum import StrEnum
from typing import Any

import pytest

from forge.domain.common import InvalidTransitionError
from forge.domain.mission import MISSION_TRANSITIONS, MissionStatus
from forge.domain.task import TASK_TRANSITIONS, TaskStatus
from tests import factories
from tests.factories import NOW

MACHINES: list[tuple[str, Any, type[StrEnum], Callable[[StrEnum], Any]]] = [
    ("mission", MISSION_TRANSITIONS, MissionStatus, lambda s: factories.mission(status=s)),
    ("task", TASK_TRANSITIONS, TaskStatus, lambda s: factories.task(status=s)),
]


def _edges(transitions: Any, states: type[StrEnum], allowed: bool) -> list[tuple[Any, Any]]:
    return [
        (source, target)
        for source in states
        for target in states
        if (target in transitions.get(source, frozenset())) == allowed
    ]


@pytest.mark.parametrize(("name", "transitions", "states", "build"), MACHINES)
def test_every_documented_edge_is_allowed(
    name: str, transitions: Any, states: type[StrEnum], build: Callable[[StrEnum], Any]
) -> None:
    for source, target in _edges(transitions, states, allowed=True):
        assert build(source).transition(target, NOW).status == target


@pytest.mark.parametrize(("name", "transitions", "states", "build"), MACHINES)
def test_every_other_edge_is_rejected(
    name: str, transitions: Any, states: type[StrEnum], build: Callable[[StrEnum], Any]
) -> None:
    for source, target in _edges(transitions, states, allowed=False):
        with pytest.raises(InvalidTransitionError):
            build(source).transition(target, NOW)


@pytest.mark.parametrize(("name", "transitions", "states", "build"), MACHINES)
def test_terminal_states_have_no_exits_and_every_live_state_can_cancel(
    name: str, transitions: Any, states: type[StrEnum], build: Callable[[StrEnum], Any]
) -> None:
    for state in states:
        exits = transitions.get(state, frozenset())
        if state.is_terminal:  # type: ignore[attr-defined]
            assert exits == frozenset()
        else:
            assert states("CANCELLED") in exits


def test_cancelled_mission_cannot_be_completed() -> None:
    cancelled = factories.mission(status=MissionStatus.CANCELLED)

    with pytest.raises(InvalidTransitionError):
        cancelled.transition(MissionStatus.COMPLETED, NOW)
