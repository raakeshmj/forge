"""A deterministic stand-in provider for demos and tests (ADR 0004).

`demo-investigator-1`: Model (requests metrics.read) -> Tool -> Model (concludes).
`demo-planner-1`: answers a planning brief with a fixed incident-investigation plan
(two parallel evidence tasks fanning in to a correlation task).
It reports usage like a real provider, including explicit prefix caching: the
prefix up to each cache breakpoint is hashed, and a prefix seen within the TTL
is reported as cache_read_input_tokens, a new one as cache_creation_input_tokens.
Its profile's minimum cacheable size applies, as it would on a real provider.
"""

import hashlib
import json
import re
import time
from collections.abc import Callable

from forge.domain.cost import Usage
from forge.domain.model import (
    ModelRequest,
    ModelResponse,
    ResponseBlock,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    block_to_json,
)
from forge.inference.profiles import DEMO_MODEL, DEMO_PLANNER, PROFILES
from forge.inference.tokens import estimate_text

METRICS_TOOL = "metrics.read"
CACHE_TTL_SECONDS = 300.0
DEMO_PLAN = json.dumps(
    {
        "tasks": [
            {
                "key": "metrics",
                "objective": "Measure checkout latency before and after the deployment.",
                "required_capabilities": ["observability"],
            },
            {
                "key": "changes",
                "objective": "List what changed in the deployment that preceded the regression.",
                "required_capabilities": ["change-management"],
            },
            {
                "key": "correlate",
                "objective": "Correlate the latency regression with the deployment changes.",
                "depends_on": ["metrics", "changes"],
                "required_capabilities": ["incident-response"],
            },
        ]
    },
    sort_keys=True,
)


class DemoInvestigationModel:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._cache: dict[str, float] = {}  # prefix hash -> expiry
        self._min_cacheable = PROFILES[DEMO_MODEL].min_cacheable_tokens

    async def complete(self, request: ModelRequest) -> ModelResponse:
        results = [
            block
            for message in request.messages
            if message.role is Role.USER
            for block in message.content
            if isinstance(block, ToolResultBlock)
        ]
        tool_names = {tool.name for tool in request.tools}
        content: tuple[ResponseBlock, ...]
        if request.model == DEMO_PLANNER:
            content = (TextBlock(DEMO_PLAN),)
            stop_reason = "end_turn"
        elif not results and METRICS_TOOL in tool_names:
            turn = sum(1 for message in request.messages if message.role is Role.ASSISTANT)
            service = _named_service(request) if _follows_service_skill(request) else "checkout"
            content = (
                TextBlock(f"Checking {service} latency around the latest deployment."),
                ToolUseBlock(
                    id=f"call_{turn}_0",
                    name=METRICS_TOOL,
                    arguments={"service": service, "metric": "latency_p95_ms", "window": "24h"},
                ),
            )
            stop_reason = "tool_use"
        elif _follows_service_skill(request):
            content = (TextBlock(_analysed_conclusion(_named_service(request), results)),)
            stop_reason = "end_turn"
        else:
            content = (TextBlock(_conclusion(results)),)
            stop_reason = "end_turn"
        output = sum(estimate_text(json.dumps(block_to_json(b), sort_keys=True)) for b in content)
        return ModelResponse(
            content=content,
            stop_reason=stop_reason,
            usage=self._cached_usage(request, output),
            provider_metadata={"simulated": True},
        )

    def _cached_usage(self, request: ModelRequest, output_tokens: int) -> Usage:
        segments = _prefix_segments(request)
        now = self._clock()
        total = segments[-1][0]
        cacheable = [(tokens, key) for tokens, key, marked in segments if marked]
        cacheable = [(tokens, key) for tokens, key in cacheable if tokens >= self._min_cacheable]
        hit = max(
            (tokens for tokens, key in cacheable if self._cache.get(key, 0.0) > now), default=0
        )
        written = max((tokens for tokens, _ in cacheable), default=0)
        for _, key in cacheable:
            self._cache[key] = now + CACHE_TTL_SECONDS  # writes and reads refresh the TTL
        return Usage(
            input_tokens=total - max(hit, written),
            output_tokens=output_tokens,
            cache_read_input_tokens=hit,
            cache_creation_input_tokens=max(0, written - hit),
        )


def _prefix_segments(request: ModelRequest) -> list[tuple[int, str, bool]]:
    """(cumulative tokens, prefix hash, ends at a breakpoint) in provider render order."""
    digest = hashlib.sha256()
    tokens = 0
    segments: list[tuple[int, str, bool]] = []

    def add(payload: object, marked: bool) -> None:
        nonlocal tokens
        text = json.dumps(payload, sort_keys=True)
        digest.update(text.encode())
        tokens += estimate_text(text)
        segments.append((tokens, digest.copy().hexdigest(), marked))

    add([[t.name, t.description, dict(t.input_schema)] for t in request.tools], False)
    for block in request.system:
        add(["system", block.key, block.text], block.cache_breakpoint)
    for index, message in enumerate(request.messages):
        last = index == len(request.messages) - 1
        add(
            [message.role.value, [block_to_json(b) for b in message.content]],
            last and request.cache.last_message,
        )
    return segments


_SERVICE_SKILL_MARKER = "service named in the objective"
_SERVICE = re.compile(r"\b([a-z][a-z-]*) latency\b")
_POINT = re.compile(r"(\d{1,2}:\d{2})=(\d+)")


def _follows_service_skill(request: ModelRequest) -> bool:
    """Simulates instruction following: a pinned skill can change how the demo investigates."""
    return any(
        block.key.startswith("skill.") and _SERVICE_SKILL_MARKER in block.text
        for block in request.system
    )


def _named_service(request: ModelRequest) -> str:
    first = request.messages[0].content[0] if request.messages else None
    match = _SERVICE.search(first.text.lower()) if isinstance(first, TextBlock) else None
    return match.group(1) if match else "checkout"


def _analysed_conclusion(service: str, results: list[ToolResultBlock]) -> str:
    """Read the series: regression after the deploy, a spike before it, or nothing."""
    usable = [r for r in results if not r.is_error]
    if not usable:
        return f"Conclusion: no {service} metrics were available. Confidence: 0.2"
    data = usable[-1].content
    deploy_at = data.find("deploy")
    points = [(m.group(1), int(m.group(2)), m.start()) for m in _POINT.finditer(data)]
    if not points:
        return f"Conclusion: the {service} metrics were unreadable. Confidence: 0.2"
    baseline = points[0][1]
    peak_time, peak, position = max(points, key=lambda p: p[1])
    if peak <= 1.5 * baseline:
        return (
            f"Conclusion: {service} latency shows no regression after the deployment "
            f"(latest {points[-1][1]}ms vs {baseline}ms).\nEvidence: {data}\nConfidence: 0.85"
        )
    if deploy_at >= 0 and position > deploy_at:
        return (
            f"Conclusion: {service} latency regressed after the deployment; p95 reached "
            f"{peak}ms at {peak_time}.\nEvidence: {data}\nConfidence: 0.85"
        )
    return (
        f"Conclusion: {service} latency spiked to {peak}ms at {peak_time}, before the "
        f"deployment, so the deployment is not the cause.\nEvidence: {data}\nConfidence: 0.85"
    )


def _conclusion(results: list[ToolResultBlock]) -> str:
    usable = [result for result in results if not result.is_error]
    if not usable:
        return "Conclusion: insufficient evidence; no metrics were available. Confidence: 0.2"
    return (
        "Conclusion: checkout p95 latency regressed after the latest deployment.\n"
        f"Evidence: {usable[-1].content}\n"
        "Confidence: 0.8"
    )
