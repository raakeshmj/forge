"""A simulated read-only metrics tool for the Phase 1 demo (the PRD's checkout scenario)."""

import asyncio
from collections.abc import Mapping

from forge.domain.common import JSONValue
from forge.domain.risk import RiskLevel
from forge.domain.tool import Idempotency, ToolDefinition, ToolResult
from forge.tools.base import ToolContext

_SERIES = {
    (
        "checkout",
        "latency_p95_ms",
    ): "13:00=210 13:30=205 14:00=212 14:05=deploy 14:10=880 14:30=905",
}


class MetricsReadTool:
    def __init__(self, *, simulated_latency_seconds: float = 0.0) -> None:
        self._latency = simulated_latency_seconds
        self._definition = ToolDefinition(
            name="metrics.read",
            version="1",
            description="Read a time series for a service metric.",
            input_schema={
                "type": "object",
                "properties": {
                    "service": {"type": "string"},
                    "metric": {"type": "string"},
                    "window": {"type": "string"},
                },
                "required": ["service", "metric"],
            },
            risk=RiskLevel.LOW,
            idempotency=Idempotency.READ_ONLY,
            timeout_seconds=max(10.0, self._latency * 2),
            max_attempts=3,
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def invoke(self, arguments: Mapping[str, JSONValue], context: ToolContext) -> ToolResult:
        service, metric = arguments.get("service"), arguments.get("metric")
        if not isinstance(service, str) or not isinstance(metric, str):
            return ToolResult("arguments 'service' and 'metric' must be strings", is_error=True)
        if self._latency:
            await asyncio.sleep(self._latency)
        series = _SERIES.get((service, metric))
        if series is None:
            return ToolResult(f"no data for {service}/{metric}", is_error=True)
        return ToolResult(f"{service} {metric}: {series}")
