"""Deterministic token estimation for planning (ADR 0004).

Providers report exact counts in usage; planning needs a stable, offline estimate.
"""

import json

from forge.domain.model import Message, block_to_json
from forge.domain.tool import ToolDefinition

CHARS_PER_TOKEN = 4


def estimate_text(text: str) -> int:
    return -(-len(text) // CHARS_PER_TOKEN) if text else 0


def estimate_messages(messages: tuple[Message, ...]) -> int:
    return sum(estimate_text(_canonical(block_to_json(b))) for m in messages for b in m.content)


def estimate_tools(tools: tuple[ToolDefinition, ...]) -> int:
    return sum(
        estimate_text(tool.name + tool.description + _canonical(dict(tool.input_schema)))
        for tool in tools
    )


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
