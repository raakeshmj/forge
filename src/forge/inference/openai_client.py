"""OpenAI Chat Completions adapter (ADR 0004).

OpenAI caches prompt prefixes automatically; the adapter forwards the cache
planner's affinity key as `prompt_cache_key`. Usage is normalised so that
input_tokens means uncached input on every provider: OpenAI's prompt_tokens
includes cached tokens, which are reported as cache_read_input_tokens instead.
"""

import json
from typing import Any

import openai
from openai import AsyncOpenAI

from forge.domain.common import JSONValue
from forge.domain.cost import Usage
from forge.domain.model import (
    Message,
    ModelRequest,
    ModelResponse,
    ResponseBlock,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from forge.inference.client import ModelCallError
from forge.inference.wire import decode_tool_name, encode_tool_name, is_retryable_status

PROVIDER = "openai"
_STOP_REASONS = {"stop": "end_turn", "tool_calls": "tool_use", "length": "max_tokens"}


class OpenAIModelClient:
    def __init__(self, client: AsyncOpenAI) -> None:
        self._client = client

    @classmethod
    def from_environment(cls) -> "OpenAIModelClient":
        return cls(AsyncOpenAI(max_retries=0))

    async def complete(self, request: ModelRequest) -> ModelResponse:
        kwargs: dict[str, Any] = {
            "model": request.model.name,
            "max_completion_tokens": request.max_output_tokens,
            "messages": [
                {"role": "system", "content": "\n\n".join(b.text for b in request.system)},
                *_messages(request.messages),
            ],
        }
        if request.tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": encode_tool_name(tool.name),
                        "description": tool.description,
                        "parameters": dict(tool.input_schema),
                    },
                }
                for tool in request.tools
            ]
        if request.cache.key is not None:
            kwargs["prompt_cache_key"] = request.cache.key
        if request.provider_options:
            kwargs["extra_body"] = dict(request.provider_options)
        try:
            completion = await self._client.chat.completions.create(**kwargs)
        except openai.APIConnectionError as exc:
            raise ModelCallError(
                f"connection failed: {type(exc).__name__}", retryable=True
            ) from exc
        except openai.APIStatusError as exc:
            raise ModelCallError(
                f"HTTP {exc.status_code}", retryable=is_retryable_status(exc.status_code)
            ) from exc

        choice = completion.choices[0]
        if choice.finish_reason == "content_filter":
            raise ModelCallError("the response was blocked by a content filter", retryable=False)
        content: list[ResponseBlock] = []
        if choice.message.content:
            content.append(TextBlock(choice.message.content))
        for call in choice.message.tool_calls or ():
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError as exc:
                raise ModelCallError(
                    "tool call arguments were not valid JSON", retryable=True
                ) from exc
            if not isinstance(arguments, dict):
                raise ModelCallError("tool call arguments were not an object", retryable=True)
            content.append(ToolUseBlock(call.id, decode_tool_name(call.function.name), arguments))

        usage = completion.usage
        prompt = usage.prompt_tokens if usage else 0
        cached = (
            (usage.prompt_tokens_details.cached_tokens or 0)
            if usage and usage.prompt_tokens_details
            else 0
        )
        raw_usage: dict[str, JSONValue] = usage.model_dump(mode="json") if usage else {}
        return ModelResponse(
            content=tuple(content),
            stop_reason=_STOP_REASONS.get(
                choice.finish_reason or "", choice.finish_reason or "unknown"
            ),
            usage=Usage(
                input_tokens=prompt - cached,
                output_tokens=usage.completion_tokens if usage else 0,
                cache_read_input_tokens=cached,
            ),
            provider_metadata={"id": completion.id, "model": completion.model, "usage": raw_usage},
        )


def _messages(messages: tuple[Message, ...]) -> list[dict[str, Any]]:
    rendered: list[dict[str, Any]] = []
    for message in messages:
        if message.role is Role.ASSISTANT:
            text = "".join(b.text for b in message.content if isinstance(b, TextBlock))
            calls = [
                {
                    "id": b.id,
                    "type": "function",
                    "function": {
                        "name": encode_tool_name(b.name),
                        "arguments": json.dumps(dict(b.arguments), sort_keys=True),
                    },
                }
                for b in message.content
                if isinstance(b, ToolUseBlock)
            ]
            entry: dict[str, Any] = {"role": "assistant", "content": text or None}
            if calls:
                entry["tool_calls"] = calls
            rendered.append(entry)
            continue
        for block in message.content:
            if isinstance(block, ToolResultBlock):
                prefix = "ERROR: " if block.is_error else ""
                rendered.append(
                    {
                        "role": "tool",
                        "tool_call_id": block.tool_use_id,
                        "content": prefix + block.content,
                    }
                )
            elif isinstance(block, TextBlock):
                rendered.append({"role": "user", "content": block.text})
    return rendered
