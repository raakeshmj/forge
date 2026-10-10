"""Benchmark-only baselines and fault injection (ADR 0013). Never used by the runtime.

`NaiveContextModel` re-lays-out each request the way blind concatenation would: a
per-request dynamic header first, then everything else, with no cache breakpoints. It
keeps every block FORGE planned, so only the layout differs from the optimised arm.
"""

import random
from collections import Counter
from collections.abc import Mapping
from dataclasses import replace

from forge.domain.common import JSONValue
from forge.domain.model import CacheHints, ModelRequest, ModelResponse, SystemBlock
from forge.domain.tool import Idempotency, ToolResult
from forge.inference.client import ModelClient
from forge.tools.base import Tool, ToolContext


class InjectedCrash(BaseException):
    """A worker dying mid-step: escapes every `except Exception`, like a killed process."""


class NaiveContextModel:
    def __init__(self, inner: ModelClient) -> None:
        self._inner = inner
        self._requests = 0

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self._requests += 1
        turns = sum(1 for _ in request.messages)
        header = SystemBlock(
            "naive.state",
            f"Request {self._requests}. Conversation so far: {turns} messages.",
        )
        system = (
            header,
            *(replace(block, cache_breakpoint=False) for block in request.system),
        )
        return await self._inner.complete(replace(request, system=system, cache=CacheHints()))


class CrashingModel:
    """Kills the worker before some model calls (seeded)."""

    def __init__(self, inner: ModelClient, rng: random.Random, rate: float) -> None:
        self._inner = inner
        self._rng = rng
        self._rate = rate
        self.crashes = 0

    async def complete(self, request: ModelRequest) -> ModelResponse:
        if self._rng.random() < self._rate:
            self.crashes += 1
            raise InjectedCrash
        return await self._inner.complete(request)


class EffectCountingTool:
    """Counts each execution per idempotency key, outside the database, and kills the worker
    before or after some of them (seeded). With `side_effect`, the tool is declared
    NON_IDEMPOTENT, so a repeated count is a duplicated side effect."""

    def __init__(self, inner: Tool, rng: random.Random, rate: float, *, side_effect: bool) -> None:
        self._inner = inner
        self._rng = rng
        self._rate = rate
        self.effects: Counter[str] = Counter()
        self.crashes = 0
        self.definition = (
            replace(inner.definition, idempotency=Idempotency.NON_IDEMPOTENT, max_attempts=1)
            if side_effect
            else inner.definition
        )

    async def invoke(self, arguments: Mapping[str, JSONValue], context: ToolContext) -> ToolResult:
        self._maybe_crash()  # before the effect: nothing happened
        result = await self._inner.invoke(arguments, context)
        self.effects[context.idempotency_key] += 1
        self._maybe_crash()  # after the effect, before its outcome is recorded
        return result

    def _maybe_crash(self) -> None:
        if self._rng.random() < self._rate:
            self.crashes += 1
            raise InjectedCrash
