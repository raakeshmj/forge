"""Contract tests: the adapters' wire format against recorded provider shapes (no network)."""

import json
from collections.abc import Callable
from typing import Any

import httpx2
import pytest
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from forge.domain.agent import ModelRef
from forge.domain.cost import Usage
from forge.domain.model import (
    CacheHints,
    Message,
    ModelRequest,
    OpaqueBlock,
    Role,
    SystemBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from forge.inference.anthropic_client import AnthropicModelClient
from forge.inference.client import ModelCallError
from forge.inference.openai_client import OpenAIModelClient
from tests import factories

pytestmark = pytest.mark.anyio

THINKING = {"type": "thinking", "thinking": "", "signature": "sig-1"}
TRANSCRIPT = (
    Message(Role.USER, (TextBlock("Why is checkout slow?"),)),
    Message(
        Role.ASSISTANT,
        (
            OpaqueBlock("anthropic", THINKING),
            TextBlock("checking"),
            ToolUseBlock("call_1", "metrics.read", {"service": "checkout"}),
        ),
    ),
    Message(Role.USER, (ToolResultBlock("call_1", "p95=880ms"),)),
)


def request(model: ModelRef, **overrides: Any) -> ModelRequest:
    base: dict[str, Any] = {
        "model": model,
        "system": (SystemBlock("a", "platform", cache_breakpoint=True), SystemBlock("b", "agent")),
        "messages": TRANSCRIPT,
        "tools": (factories.tool_definition(),),
        "max_output_tokens": 1024,
        "cache": CacheHints(last_message=True, key="affinity"),
        "provider_options": {"output_config": {"effort": "high"}},
    }
    base.update(overrides)
    return ModelRequest(**base)


class Recorder:
    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status, self.body = status, body
        self.sent: dict[str, Any] = {}

    def __call__(self, http_request: httpx2.Request) -> httpx2.Response:
        self.sent = json.loads(http_request.content)
        return httpx2.Response(self.status, json=self.body)


def http(handler: Callable[[httpx2.Request], httpx2.Response]) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


# --- Anthropic ---------------------------------------------------------------------------

ANTHROPIC = ModelRef("anthropic", "claude-sonnet-5-5")
ANTHROPIC_REPLY = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": "claude-sonnet-5-5",
    "content": [
        {"type": "thinking", "thinking": "", "signature": "sig-2"},
        {"type": "text", "text": "Need logs too."},
        {
            "type": "tool_use",
            "id": "toolu_9",
            "name": "logs__read",
            "input": {"service": "checkout"},
        },
    ],
    "stop_reason": "tool_use",
    "stop_sequence": None,
    "usage": {
        "input_tokens": 50,
        "output_tokens": 20,
        "cache_read_input_tokens": 1000,
        "cache_creation_input_tokens": 200,
    },
}


def anthropic(recorder: Callable[[httpx2.Request], httpx2.Response]) -> AnthropicModelClient:
    return AnthropicModelClient(
        AsyncAnthropic(api_key="test", max_retries=0, http_client=http(recorder))
    )


async def test_anthropic_request_places_cache_control_and_preserves_provider_blocks() -> None:
    recorder = Recorder(200, ANTHROPIC_REPLY)

    await anthropic(recorder).complete(request(ANTHROPIC))

    sent = recorder.sent
    assert sent["model"] == "claude-sonnet-5-5"
    assert sent["output_config"] == {"effort": "high"}  # provider options pass through
    assert sent["system"] == [
        {"type": "text", "text": "platform", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "agent"},
    ]
    assert sent["tools"][0]["name"] == "metrics__read"
    assistant = sent["messages"][1]["content"]
    assert assistant[0] == THINKING  # replayed verbatim to the provider that produced it
    assert assistant[2] == {
        "type": "tool_use",
        "id": "call_1",
        "name": "metrics__read",
        "input": {"service": "checkout"},
    }
    assert sent["messages"][2]["content"][-1]["cache_control"] == {"type": "ephemeral"}


async def test_anthropic_response_maps_blocks_and_cache_usage() -> None:
    response = await anthropic(Recorder(200, ANTHROPIC_REPLY)).complete(request(ANTHROPIC))

    assert response.content[0] == OpaqueBlock(
        "anthropic", {"type": "thinking", "thinking": "", "signature": "sig-2"}
    )
    assert response.tool_uses == (ToolUseBlock("toolu_9", "logs.read", {"service": "checkout"}),)
    assert response.usage == Usage(50, 20, 1000, 200)
    assert response.provider_metadata["id"] == "msg_1"


async def test_other_providers_opaque_blocks_are_not_sent_to_anthropic() -> None:
    recorder = Recorder(200, ANTHROPIC_REPLY)
    foreign = (Message(Role.ASSISTANT, (OpaqueBlock("openai", {"x": 1}), TextBlock("hi"))),)

    await anthropic(recorder).complete(request(ANTHROPIC, messages=(TRANSCRIPT[0], *foreign)))

    assert recorder.sent["messages"][1]["content"][0]["type"] == "text"


@pytest.mark.parametrize(
    ("status", "retryable"), [(429, True), (529, True), (503, True), (400, False), (401, False)]
)
async def test_anthropic_errors_are_classified(status: int, retryable: bool) -> None:
    body = {"type": "error", "error": {"type": "x", "message": "x"}}

    with pytest.raises(ModelCallError) as excinfo:
        await anthropic(Recorder(status, body)).complete(request(ANTHROPIC))

    assert excinfo.value.retryable is retryable


async def test_anthropic_refusal_is_final() -> None:
    refused = {**ANTHROPIC_REPLY, "content": [], "stop_reason": "refusal"}

    with pytest.raises(ModelCallError) as excinfo:
        await anthropic(Recorder(200, refused)).complete(request(ANTHROPIC))

    assert not excinfo.value.retryable


async def test_connection_failures_are_retryable() -> None:
    def fail(_: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("unreachable")

    with pytest.raises(ModelCallError) as excinfo:
        await anthropic(fail).complete(request(ANTHROPIC))

    assert excinfo.value.retryable


# --- OpenAI ------------------------------------------------------------------------------

OPENAI = ModelRef("openai", "gpt-5")


def openai_reply(**message: Any) -> dict[str, Any]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1,
        "model": "gpt-5",
        "choices": [
            {
                "index": 0,
                "finish_reason": message.pop("finish_reason", "tool_calls"),
                "message": {"role": "assistant", "content": None, **message},
            }
        ],
        "usage": {
            "prompt_tokens": 2000,
            "completion_tokens": 30,
            "total_tokens": 2030,
            "prompt_tokens_details": {"cached_tokens": 1500},
        },
    }


TOOL_CALL = {
    "tool_calls": [
        {
            "id": "call_2",
            "type": "function",
            "function": {"name": "metrics__read", "arguments": '{"service":"checkout"}'},
        }
    ]
}


def openai_client(recorder: Callable[[httpx2.Request], httpx2.Response]) -> OpenAIModelClient:
    return OpenAIModelClient(AsyncOpenAI(api_key="test", max_retries=0, http_client=http(recorder)))


async def test_openai_request_shape() -> None:
    recorder = Recorder(200, openai_reply(**TOOL_CALL))

    await openai_client(recorder).complete(request(OPENAI, provider_options={}))

    sent = recorder.sent
    assert sent["prompt_cache_key"] == "affinity"
    assert sent["max_completion_tokens"] == 1024
    assert [m["role"] for m in sent["messages"]] == ["system", "user", "assistant", "tool"]
    assert sent["messages"][0]["content"] == "platform\n\nagent"
    assistant = sent["messages"][2]
    assert assistant["tool_calls"][0]["function"] == {
        "name": "metrics__read",
        "arguments": '{"service": "checkout"}',
    }
    assert "thinking" not in json.dumps(assistant)  # Anthropic's opaque block is not forwarded
    assert sent["messages"][3] == {"role": "tool", "tool_call_id": "call_1", "content": "p95=880ms"}
    assert sent["tools"][0]["function"]["name"] == "metrics__read"


async def test_openai_usage_is_normalised_so_cached_tokens_are_not_counted_twice() -> None:
    response = await openai_client(Recorder(200, openai_reply(**TOOL_CALL))).complete(
        request(OPENAI)
    )

    assert response.usage == Usage(input_tokens=500, output_tokens=30, cache_read_input_tokens=1500)
    assert response.tool_uses == (ToolUseBlock("call_2", "metrics.read", {"service": "checkout"}),)
    assert response.stop_reason == "tool_use"
    raw = response.provider_metadata["usage"]
    assert isinstance(raw, dict)
    details = raw["prompt_tokens_details"]
    assert isinstance(details, dict)
    assert details["cached_tokens"] == 1500  # raw provider usage is preserved


async def test_openai_content_filter_is_final_and_bad_arguments_are_retryable() -> None:
    with pytest.raises(ModelCallError) as blocked:
        await openai_client(Recorder(200, openai_reply(finish_reason="content_filter"))).complete(
            request(OPENAI)
        )
    bad = {
        "tool_calls": [
            {"id": "c", "type": "function", "function": {"name": "x", "arguments": "{not json"}}
        ]
    }
    with pytest.raises(ModelCallError) as malformed:
        await openai_client(Recorder(200, openai_reply(**bad))).complete(request(OPENAI))

    assert not blocked.value.retryable
    assert malformed.value.retryable
