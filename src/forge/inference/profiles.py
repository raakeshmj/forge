"""Model profiles: what each model can do and what it costs (ADR 0004).

Prices are micro-USD per million tokens. They are configuration with an as-of
date, not facts: update them when providers change pricing. A model without a
profile is never routed to.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from forge.domain.agent import ModelRef
from forge.domain.cost import ModelPrice

PRICES_AS_OF = "2026-10-08"


class CachingMode(StrEnum):
    EXPLICIT = "explicit"  # caller places cache breakpoints (Anthropic cache_control)
    AUTOMATIC = "automatic"  # provider caches prefixes itself (OpenAI)
    NONE = "none"


@dataclass(frozen=True, slots=True)
class ModelProfile:
    model: ModelRef
    context_window_tokens: int
    max_output_tokens: int
    price: ModelPrice
    supports_tools: bool
    caching: CachingMode
    min_cacheable_tokens: int
    quality_tier: int  # 1 (fast, cheap) .. 3 (most capable)


DEMO_MODEL = ModelRef("demo", "demo-investigator-1")


def _usd(input_: float, output: float, cache_read: float, cache_write: float) -> ModelPrice:
    return ModelPrice(
        input=round(input_ * 1_000_000),
        output=round(output * 1_000_000),
        cache_read=round(cache_read * 1_000_000),
        cache_write=round(cache_write * 1_000_000),
    )


def _profiles(*profiles: ModelProfile) -> Mapping[ModelRef, ModelProfile]:
    return {profile.model: profile for profile in profiles}


PROFILES: Mapping[ModelRef, ModelProfile] = _profiles(
    # Deterministic local fake (forge.inference.demo) with simulated prefix caching.
    ModelProfile(
        DEMO_MODEL, 32_000, 2_048, _usd(1, 5, 0.1, 1.25), True, CachingMode.EXPLICIT, 256, 1
    ),
    # Anthropic: cache writes are 1.25x input for the default 5-minute TTL.
    ModelProfile(
        ModelRef("anthropic", "claude-opus-5-5"),
        1_000_000,
        128_000,
        _usd(4, 20, 0.20, 5),
        True,
        CachingMode.EXPLICIT,
        512,
        3,
    ),
    ModelProfile(
        ModelRef("anthropic", "claude-sonnet-5-5"),
        1_000_000,
        128_000,
        _usd(2, 10, 0.20, 2.5),
        True,
        CachingMode.EXPLICIT,
        512,
        2,
    ),
    ModelProfile(
        ModelRef("anthropic", "claude-haiku-4-5"),
        200_000,
        64_000,
        _usd(1, 5, 0.10, 1.25),
        True,
        CachingMode.EXPLICIT,
        4_096,
        1,
    ),
    # OpenAI: automatic prefix caching from 1,024 tokens, no write premium.
    ModelProfile(
        ModelRef("openai", "gpt-5"),
        400_000,
        128_000,
        _usd(1.25, 10, 0.125, 0),
        True,
        CachingMode.AUTOMATIC,
        1_024,
        3,
    ),
    ModelProfile(
        ModelRef("openai", "gpt-5-mini"),
        400_000,
        128_000,
        _usd(0.25, 2, 0.025, 0),
        True,
        CachingMode.AUTOMATIC,
        1_024,
        2,
    ),
)
