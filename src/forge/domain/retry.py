"""Bounded exponential backoff."""

from dataclasses import dataclass

from forge.domain.common import DomainError


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int
    initial_backoff_seconds: float
    max_backoff_seconds: float

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise DomainError("max_attempts must be at least 1")
        if self.initial_backoff_seconds < 0 or self.max_backoff_seconds < 0:
            raise DomainError("backoff must not be negative")

    def backoff_after(self, failed_attempt: int) -> float:
        """Delay before the attempt following `failed_attempt` (1-based): doubles, capped."""
        delay: float = self.initial_backoff_seconds * 2 ** (failed_attempt - 1)
        return min(delay, self.max_backoff_seconds)
