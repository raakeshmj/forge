"""Anthropic Messages API adapter (ADR 0004).

Explicit prompt caching: `cache_control` is placed where the cache planner put
breakpoints (system blocks and the last message). Content the domain does not
model (thinking blocks and any future block type) round-trips as OpaqueBlock.
SDK retries are disabled: the runtime owns retry policy.
"""

from typing import Any

import anthropic
from anthropic import AsyncAnthropic

from forge.domain.common import JSONValue
from forge.domain.cost import Usage
from forge.domain.model import (
    ContentBlock,
    Message,
    ModelRequest,
    ModelResponse,
    OpaqueBlock,
    ResponseBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from forge.inference.client import ModelCallError
from forge.inference.wire import decode_tool_name, encode_tool_name, is_retryable_status

PROVIDER = "anthropic"
_EPHEMERAL = {"type": "ephemeral"}


class AnthropicModelClient:
    def __init__(self, client: AsyncAnthropic) -> None:
        self._client = client

    @classmethod
    def from_environment(cls) -> "AnthropicModelClient":
        """Credentials come from the SDK's standard resolution (env vars or a CLI profile)."""
        return cls(AsyncAnthropic(max_retries=0))

    async def complete(self, request: ModelRequest) -> ModelResponse:
        kwargs: dict[str, Any] = {
            "model": request.model.name,
            "max_tokens": request.max_output_tokens,
            "system": [_system_block(b.text, b.cache_breakpoint) for b in request.system],
            "messages": _messages(request.messages, cache_last=request.cache.last_message),
        }
        if request.tools:
            kwargs["tools"] = [
                {
                    "name": encode_tool_name(tool.name),
                    "description": tool.description,
                    "input_schema": dict(tool.input_schema),
                }
                for tool in request.tools
            ]
        if request.provider_options:
            kwargs["extra_body"] = dict(request.provider_options)
        try:
            message = await self._client.messages.create(**kwargs)
        except anthropic.APIConnectionError as exc:  # includes timeouts
            raise ModelCallError(
                f"connection failed: {type(exc).__name__}", retryable=True
            ) from exc
        except anthropic.APIStatusError as exc:
            raise ModelCallError(
                f"HTTP {exc.status_code}", retryable=is_retryable_status(exc.status_code)
            ) from exc

        if message.stop_reason == "refusal":
            raise ModelCallError("the model declined the request", retryable=False)
        usage = message.usage
        return ModelResponse(
            content=tuple(_response_block(block) for block in message.content),
            stop_reason=message.stop_reason or "unknown",
            usage=Usage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_input_tokens=usage.cache_read_input_tokens or 0,
                cache_creation_input_tokens=usage.cache_creation_input_tokens or 0,
            ),
            provider_metadata={
                "id": message.id,
                "model": message.model,
                "usage": _json(usage.to_dict()),
            },
        )


def _system_block(text: str, breakpoint: bool) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "text", "text": text}
    if breakpoint:
        block["cache_control"] = _EPHEMERAL
    return block


def _messages(messages: tuple[Message, ...], *, cache_last: bool) -> list[dict[str, Any]]:
    contents: list[list[dict[str, Any]]] = [
        [b for b in (_block(block) for block in message.content) if b is not None]
        for message in messages
    ]
    if cache_last and contents and contents[-1]:
        contents[-1][-1] = {**contents[-1][-1], "cache_control": _EPHEMERAL}
    return [
        {"role": message.role.value, "content": blocks}
        for message, blocks in zip(messages, contents, strict=True)
    ]


def _block(block: ContentBlock) -> dict[str, Any] | None:
    match block:
        case TextBlock(text=text):
            return {"type": "text", "text": text}
        case ToolUseBlock(id=block_id, name=name, arguments=arguments):
            return {
                "type": "tool_use",
                "id": block_id,
                "name": encode_tool_name(name),
                "input": dict(arguments),
            }
        case ToolResultBlock(tool_use_id=tool_use_id, content=content, is_error=is_error):
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": content,
                "is_error": is_error,
            }
        case OpaqueBlock(provider=provider, data=data):
            return dict(data) if provider == PROVIDER else None


def _response_block(block: Any) -> ResponseBlock:
    if block.type == "text":
        return TextBlock(block.text)
    if block.type == "tool_use":
        return ToolUseBlock(block.id, decode_tool_name(block.name), _json(dict(block.input)))
    return OpaqueBlock(PROVIDER, _json(block.to_dict()))  # thinking and future block types


def _json(value: dict[str, Any]) -> dict[str, JSONValue]:
    return value
