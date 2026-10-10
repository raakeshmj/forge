"""External events for the Temporal backend: signal the affected task workflows (ADR 0011)."""

import asyncio
import logging
from collections.abc import Sequence

from temporalio.client import Client
from temporalio.service import RPCError

from forge.domain.execution import Execution
from forge.infrastructure.temporal.workflows import WAKE, workflow_id

_log = logging.getLogger(__name__)


class TemporalWakeup:
    """Connects on first use, so the API starts (and keeps serving) while Temporal is down."""

    def __init__(self, address: str, namespace: str) -> None:
        self._address = address
        self._namespace = namespace
        self._client: Client | None = None
        self._connecting = asyncio.Lock()

    async def wake(self, executions: Sequence[Execution]) -> None:
        if not executions:
            return
        try:
            client = await self._connected()
        except RuntimeError as exc:  # recovery: best effort; the workflows' poll catches up
            _log.warning(
                "temporal unreachable; wake signals skipped",
                extra={"error_type": type(exc).__name__, "retryable": True},
            )
            return
        for execution in executions:
            handle = client.get_workflow_handle(
                workflow_id(str(execution.task_id), execution.task_attempt)
            )
            try:
                await handle.signal(WAKE)
            except RPCError as exc:  # best effort: the workflow's poll picks the change up
                _log.warning(
                    "wake signal not delivered",
                    extra={
                        "execution_id": str(execution.id),
                        "error_type": exc.status.name,
                        "retryable": True,
                    },
                )

    async def _connected(self) -> Client:
        async with self._connecting:
            if self._client is None:
                self._client = await Client.connect(self._address, namespace=self._namespace)
            return self._client
