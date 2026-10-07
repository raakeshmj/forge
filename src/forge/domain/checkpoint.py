"""Agent state and immutable checkpoints (docs/DOMAIN_MODEL.md §14).

The state is the full transcript: it is both what resume continues from and the
trajectory record later phases read (ADR 0003). Pending work is derived from
the transcript, never stored separately, so the two cannot disagree.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from forge.domain.common import DomainError, JSONObject
from forge.domain.model import (
    Message,
    ModelResponse,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    message_from_json,
    message_to_json,
)

STATE_VERSION = 1


@dataclass(frozen=True, slots=True)
class AgentState:
    messages: tuple[Message, ...]

    @classmethod
    def initial(cls, objective: str) -> "AgentState":
        return cls((Message(Role.USER, (TextBlock(objective),)),))

    @property
    def turns(self) -> int:
        """Completed model turns."""
        return sum(1 for message in self.messages if message.role is Role.ASSISTANT)

    def pending_tool_uses(self) -> tuple[ToolUseBlock, ...]:
        """Tool calls from the latest model turn that have no recorded result yet."""
        last_assistant = self._last_assistant_index()
        if last_assistant is None:
            return ()
        requested = [
            block
            for block in self.messages[last_assistant].content
            if isinstance(block, ToolUseBlock)
        ]
        answered = {
            block.tool_use_id
            for message in self.messages[last_assistant + 1 :]
            for block in message.content
            if isinstance(block, ToolResultBlock)
        }
        return tuple(block for block in requested if block.id not in answered)

    def with_model_response(self, response: ModelResponse) -> "AgentState":
        if self.pending_tool_uses():
            raise DomainError("cannot record a model turn while tool calls are pending")
        return AgentState((*self.messages, Message(Role.ASSISTANT, response.content)))

    def with_tool_result(self, result: ToolResultBlock) -> "AgentState":
        if result.tool_use_id not in {block.id for block in self.pending_tool_uses()}:
            raise DomainError(f"no pending tool call {result.tool_use_id!r}")
        last = self.messages[-1]
        if last.role is Role.USER:  # results of the same turn share one user message
            return AgentState((*self.messages[:-1], Message(Role.USER, (*last.content, result))))
        return AgentState((*self.messages, Message(Role.USER, (result,))))

    def to_json(self) -> JSONObject:
        return {"messages": [message_to_json(message) for message in self.messages]}

    @classmethod
    def from_json(cls, data: JSONObject) -> "AgentState":
        messages = data.get("messages")
        if not isinstance(messages, list):
            raise DomainError("malformed agent state")
        parsed = []
        for item in messages:
            if not isinstance(item, dict):
                raise DomainError("malformed agent state message")
            parsed.append(message_from_json(item))
        return cls(tuple(parsed))

    def _last_assistant_index(self) -> int | None:
        for index in range(len(self.messages) - 1, -1, -1):
            if self.messages[index].role is Role.ASSISTANT:
                return index
        return None


@dataclass(frozen=True, slots=True)
class Checkpoint:
    execution_id: UUID
    sequence: int
    state: AgentState
    state_version: int
    created_at: datetime
