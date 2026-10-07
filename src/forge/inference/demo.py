"""A deterministic stand-in model for the Phase 1 demonstration flow.

Mission -> Model (requests metrics.read) -> Tool -> Model (concludes) -> Result.
It reports usage like a provider would so cost accounting and budgets are exercised.
"""

from forge.domain.cost import Usage
from forge.domain.model import (
    ModelRequest,
    ModelResponse,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    block_to_json,
)

METRICS_TOOL = "metrics.read"
_CHARS_PER_TOKEN = 4


class DemoInvestigationModel:
    async def complete(self, request: ModelRequest) -> ModelResponse:
        results = [
            block
            for message in request.messages
            if message.role is Role.USER
            for block in message.content
            if isinstance(block, ToolResultBlock)
        ]
        tool_names = {tool.name for tool in request.tools}
        if not results and METRICS_TOOL in tool_names:
            turn = sum(1 for message in request.messages if message.role is Role.ASSISTANT)
            content: tuple[TextBlock | ToolUseBlock, ...] = (
                TextBlock("Checking checkout latency around the latest deployment."),
                ToolUseBlock(
                    id=f"call_{turn}_0",
                    name=METRICS_TOOL,
                    arguments={"service": "checkout", "metric": "latency_p95_ms", "window": "24h"},
                ),
            )
            stop_reason = "tool_use"
        else:
            content = (TextBlock(_conclusion(results)),)
            stop_reason = "end_turn"
        return ModelResponse(
            content=content,
            stop_reason=stop_reason,
            usage=Usage(
                input_tokens=_estimate_tokens(request),
                output_tokens=sum(_tokens(str(block_to_json(b))) for b in content),
            ),
        )


def _conclusion(results: list[ToolResultBlock]) -> str:
    usable = [result for result in results if not result.is_error]
    if not usable:
        return "Conclusion: insufficient evidence; no metrics were available."
    return (
        "Conclusion: checkout p95 latency regressed after the latest deployment.\n"
        f"Evidence: {usable[-1].content}"
    )


def _tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _estimate_tokens(request: ModelRequest) -> int:
    text = request.system + "".join(
        str(block_to_json(block)) for message in request.messages for block in message.content
    )
    return _tokens(text) + sum(_tokens(tool.description) for tool in request.tools)
