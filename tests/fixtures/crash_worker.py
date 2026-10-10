"""A worker process for crash tests: `python -m tests.fixtures.crash_worker`.

Runs the real runtime on either backend with a stateless model (call the tool once,
then answer) and one tool whose effects are appended to a file outside the database,
so a test can SIGKILL the process mid-effect and count what really happened.

Environment: FORGE_DATABASE_URL, FORGE_EXECUTION_BACKEND, FORGE_TEMPORAL_ADDRESS,
FORGE_TEMPORAL_TASK_QUEUE, CRASH_TOOL_IDEMPOTENCY, CRASH_EFFECTS_FILE, CRASH_HANG_FILE.
"""

import asyncio
import os
import signal
from collections.abc import Mapping
from pathlib import Path

from forge.domain.common import JSONValue
from forge.domain.cost import Usage
from forge.domain.model import ModelRequest, ModelResponse, TextBlock, ToolResultBlock, ToolUseBlock
from forge.domain.risk import RiskLevel
from forge.domain.tool import Idempotency, ToolDefinition, ToolResult
from forge.infrastructure import postgres
from forge.infrastructure.clock import SystemClock
from forge.infrastructure.store import PostgresUnitOfWork
from forge.infrastructure.temporal import service
from forge.infrastructure.temporal.dispatch import DispatchSettings
from forge.runtime.agent_loop import Runtime
from forge.runtime.worker import Worker
from forge.tools.base import ToolContext
from tests.runtime_doubles import TEST_MODEL, TEST_PROFILE

TOOL = "ledger.append"
LEASE_SECONDS = 1.0


class StatelessModel:
    async def complete(self, request: ModelRequest) -> ModelResponse:
        seen = any(isinstance(b, ToolResultBlock) for m in request.messages for b in m.content)
        usage = Usage(input_tokens=10, output_tokens=5)
        if seen:
            return ModelResponse((TextBlock("done"),), "end_turn", usage)
        call = ToolUseBlock("c1", TOOL, {"entry": "x"})
        return ModelResponse((TextBlock("appending"), call), "tool_use", usage)


class EffectTool:
    def __init__(self, idempotency: Idempotency, effects: Path, hang: Path) -> None:
        self._effects = effects
        self._hang = hang
        self.definition = ToolDefinition(
            name=TOOL,
            version="1",
            description="Append an entry",
            input_schema={"type": "object"},
            risk=RiskLevel.LOW,
            idempotency=idempotency,
            timeout_seconds=3600.0,
        )

    async def invoke(self, arguments: Mapping[str, JSONValue], context: ToolContext) -> ToolResult:
        with self._effects.open("a") as ledger:  # the side effect, outside FORGE's database
            ledger.write(f"{context.idempotency_key}\n")
        if self._hang.exists():
            self._hang.write_text("hanging")  # the test SIGKILLs us now
            await asyncio.sleep(3600)
        return ToolResult("appended")


async def main() -> None:
    env = os.environ
    engine = postgres.create_engine(env["FORGE_DATABASE_URL"])
    tool = EffectTool(
        Idempotency(env["CRASH_TOOL_IDEMPOTENCY"]),
        Path(env["CRASH_EFFECTS_FILE"]),
        Path(env["CRASH_HANG_FILE"]),
    )
    rt = Runtime(
        uow_factory=lambda: PostgresUnitOfWork(engine),
        clock=SystemClock(),
        models={TEST_MODEL.provider: StatelessModel()},
        profiles={TEST_MODEL: TEST_PROFILE},
        tools={TOOL: tool},
        worker_id=f"crash-{os.getpid()}",
        lease_seconds=LEASE_SECONDS,
    )
    stop = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)
    if env["FORGE_EXECUTION_BACKEND"] == "temporal":
        client = await service.connect(env["FORGE_TEMPORAL_ADDRESS"], "default")
        dispatch = DispatchSettings(
            task_queue=env["FORGE_TEMPORAL_TASK_QUEUE"],
            poll_interval_seconds=0.1,
            heartbeat_seconds=LEASE_SECONDS,
        )
        await service.run_until(client, rt, dispatch, stop, concurrency=2)
    else:
        await Worker(rt, poll_interval_seconds=0.05).run_until(stop)


if __name__ == "__main__":
    asyncio.run(main())
