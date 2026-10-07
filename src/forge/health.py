"""Dependency health-check contract shared by the API and infrastructure adapters."""

from typing import Protocol


class DependencyUnavailableError(Exception):
    """A dependency failed its health check in an expected way (unreachable, refused, erroring)."""

    def __init__(self, dependency: str) -> None:
        super().__init__(f"{dependency} is unavailable")
        self.dependency = dependency


class DependencyCheck(Protocol):
    """A cheap round trip to one external dependency."""

    @property
    def name(self) -> str: ...

    async def check(self) -> None:
        """Return if healthy; raise `DependencyUnavailableError` (chained to the cause) if not."""
        ...
