"""Provider-neutral model request/response types.

Common behaviour is normalised here; provider-specific detail travels in
`ModelResponse.provider_metadata` and the cache fields of `Usage` so adapters
never have to discard it (CLAUDE.md §6).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from forge.domain.agent import ModelRef
from forge.domain.common import DomainError, JSONObject, JSONValue
from forge.domain.cost import Usage
from forge.domain.tool import ToolDefinition


class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class TextBlock:
    text: str


@dataclass(frozen=True, slots=True)
class ToolUseBlock:
    id: str
    name: str
    arguments: Mapping[str, JSONValue]


@dataclass(frozen=True, slots=True)
class ToolResultBlock:
    """A tool's output, returned to the model as data, never as instructions."""

    tool_use_id: str
    content: str
    is_error: bool = False


@dataclass(frozen=True, slots=True)
class OpaqueBlock:
    """Provider-specific content the domain does not model (e.g. Anthropic thinking blocks).

    Kept verbatim in the transcript and replayed only to the provider that produced it.
    """

    provider: str
    data: Mapping[str, JSONValue]


type ContentBlock = TextBlock | ToolUseBlock | ToolResultBlock | OpaqueBlock
type ResponseBlock = TextBlock | ToolUseBlock | OpaqueBlock


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    content: tuple[ContentBlock, ...]


@dataclass(frozen=True, slots=True)
class SystemBlock:
    """One block of system context. `cache_breakpoint` marks the end of a cacheable prefix."""

    key: str
    text: str
    cache_breakpoint: bool = False


@dataclass(frozen=True, slots=True)
class CacheHints:
    """Provider-neutral caching intent produced by the cache planner (ADR 0004)."""

    tools: bool = False  # breakpoint after the tool definitions
    last_message: bool = False  # breakpoint on the final message (incremental transcript caching)
    key: str | None = None  # cache-affinity key for automatic-caching providers


@dataclass(frozen=True, slots=True)
class ModelRequest:
    model: ModelRef
    system: tuple[SystemBlock, ...]
    messages: tuple[Message, ...]
    tools: tuple[ToolDefinition, ...]
    max_output_tokens: int
    cache: CacheHints = CacheHints()
    provider_options: Mapping[str, JSONValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ModelResponse:
    content: tuple[ResponseBlock, ...]
    stop_reason: str
    usage: Usage
    provider_metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    @property
    def tool_uses(self) -> tuple[ToolUseBlock, ...]:
        return tuple(block for block in self.content if isinstance(block, ToolUseBlock))

    @property
    def text(self) -> str:
        return "".join(block.text for block in self.content if isinstance(block, TextBlock))


def block_to_json(block: ContentBlock) -> JSONObject:
    match block:
        case TextBlock(text=text):
            return {"type": "text", "text": text}
        case ToolUseBlock(id=block_id, name=name, arguments=arguments):
            return {"type": "tool_use", "id": block_id, "name": name, "arguments": dict(arguments)}
        case ToolResultBlock(tool_use_id=tool_use_id, content=content, is_error=is_error):
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": content,
                "is_error": is_error,
            }
        case OpaqueBlock(provider=provider, data=data):
            return {"type": "opaque", "provider": provider, "data": dict(data)}


def block_from_json(data: JSONObject) -> ContentBlock:
    match data:
        case {"type": "text", "text": str(text)}:
            return TextBlock(text)
        case {"type": "tool_use", "id": str(block_id), "name": str(name), "arguments": dict(args)}:
            return ToolUseBlock(block_id, name, args)
        case {
            "type": "tool_result",
            "tool_use_id": str(tool_use_id),
            "content": str(content),
            "is_error": bool(is_error),
        }:
            return ToolResultBlock(tool_use_id, content, is_error)
        case {"type": "opaque", "provider": str(provider), "data": dict(data)}:
            return OpaqueBlock(provider, data)
    raise DomainError(f"unrecognised content block: {data.get('type')!r}")


def message_to_json(message: Message) -> JSONObject:
    return {"role": message.role.value, "content": [block_to_json(b) for b in message.content]}


def message_from_json(data: JSONObject) -> Message:
    role, content = data.get("role"), data.get("content")
    if not isinstance(role, str) or not isinstance(content, list):
        raise DomainError("malformed message")
    blocks = []
    for item in content:
        if not isinstance(item, dict):
            raise DomainError("malformed content block")
        blocks.append(block_from_json(item))
    return Message(Role(role), tuple(blocks))
