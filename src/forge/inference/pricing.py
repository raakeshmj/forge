"""Model prices. A model with no price is never called (ADR 0002): spend must be accountable."""

from collections.abc import Mapping

from forge.domain.agent import ModelRef
from forge.domain.cost import ModelPrice

DEMO_MODEL = ModelRef("demo", "demo-investigator-1")

PRICES: Mapping[ModelRef, ModelPrice] = {
    # Nominal prices so the demo exercises cost accounting and budgets like a real model.
    DEMO_MODEL: ModelPrice(
        input=1_000_000, output=5_000_000, cache_read=100_000, cache_write=1_250_000
    ),
}
