"""Live provider checks. They cost money and need credentials: run with `make test-live`.

Each sends the same cache-marked request twice and asserts the second read the cache.
"""

import os

import pytest

from forge.domain.agent import ModelRef
from forge.domain.checkpoint import AgentState
from forge.domain.model import ModelRequest
from forge.inference.anthropic_client import AnthropicModelClient
from forge.inference.client import ModelClient
from forge.inference.context import PlanningInputs, build_request, plan_cache, plan_context
from forge.inference.openai_client import OpenAIModelClient
from forge.inference.profiles import PROFILES, ModelProfile
from tests import factories

pytestmark = [pytest.mark.live, pytest.mark.anyio]

LONG_INSTRUCTIONS = "Investigate production incidents methodically from evidence. " * 300


def _request(profile: ModelProfile) -> ModelRequest:
    _, version = factories.agent_and_version(instructions=LONG_INSTRUCTIONS, model=profile.model)
    inputs = PlanningInputs(
        version, factories.mission(), (), AgentState.initial("Reply with the word ok.").messages
    )
    plan, messages = plan_context(inputs, budget_tokens=100_000)
    plan, hints = plan_cache(plan, profile)
    return build_request(
        plan, hints, messages, inputs=inputs, profile=profile, max_output_tokens=256
    )


async def _assert_cache_reuse(client: ModelClient, profile: ModelProfile) -> None:
    request = _request(profile)
    await client.complete(request)
    second = await client.complete(request)
    assert second.usage.cache_read_input_tokens > 0


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY")
async def test_anthropic_prompt_cache_is_read_on_repeat() -> None:
    profile = PROFILES[ModelRef("anthropic", "claude-haiku-4-5")]
    await _assert_cache_reuse(AnthropicModelClient.from_environment(), profile)


@pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="needs OPENAI_API_KEY")
async def test_openai_prompt_cache_is_read_on_repeat() -> None:
    profile = PROFILES[ModelRef("openai", "gpt-5-mini")]
    await _assert_cache_reuse(OpenAIModelClient.from_environment(), profile)
