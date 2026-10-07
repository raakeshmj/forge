"""The model provider port."""

from typing import Protocol

from forge.domain.model import ModelRequest, ModelResponse


class ModelCallError(Exception):
    """A provider call failed. `retryable` distinguishes transient faults from permanent ones."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class ModelClient(Protocol):
    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Run one completion. Raises ModelCallError; never retries internally."""
        ...
