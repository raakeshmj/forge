"""Token usage and cost. Money is integer micro-USD so sums are exact."""

from dataclasses import dataclass

from forge.domain.common import DomainError

MICRO_USD_PER_USD = 1_000_000
_TOKENS_PER_PRICE_UNIT = 1_000_000  # prices are quoted per million tokens


@dataclass(frozen=True, slots=True)
class Usage:
    """Provider-reported usage. Cache fields are kept even when a provider reports zero."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_input_tokens=self.cache_read_input_tokens + other.cache_read_input_tokens,
            cache_creation_input_tokens=self.cache_creation_input_tokens
            + other.cache_creation_input_tokens,
        )

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_input_tokens
            + self.cache_creation_input_tokens
        )


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """Micro-USD per million tokens, per usage category."""

    input: int
    output: int
    cache_read: int
    cache_write: int

    def __post_init__(self) -> None:
        if min(self.input, self.output, self.cache_read, self.cache_write) < 0:
            raise DomainError("prices must not be negative")

    def cost_of(self, usage: Usage) -> int:
        """Exact cost in micro-USD, rounded up so accounting never under-reports spend."""
        numerator = (
            usage.input_tokens * self.input
            + usage.output_tokens * self.output
            + usage.cache_read_input_tokens * self.cache_read
            + usage.cache_creation_input_tokens * self.cache_write
        )
        return -(-numerator // _TOKENS_PER_PRICE_UNIT)
