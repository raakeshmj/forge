"""Health endpoints.

Liveness says the process serves requests; readiness says its dependencies answer.
"""

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from forge.health import DependencyCheck, DependencyUnavailableError

router = APIRouter(prefix="/health", tags=["health"])

_log = logging.getLogger(__name__)


@router.get("/live")
async def live() -> dict[str, str]:
    """Liveness: the process is serving requests. Never checks dependencies."""
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    """Readiness: every dependency answered within the timeout. 503 otherwise."""
    checks: Sequence[DependencyCheck] = request.app.state.readiness_checks
    timeout: float = request.app.state.readiness_timeout_seconds
    results = await asyncio.gather(*(_run(check, timeout) for check in checks))
    is_ready = all(result["status"] == "ok" for _, result in results)
    return JSONResponse(
        status_code=200 if is_ready else 503,
        content={"status": "ready" if is_ready else "not_ready", "checks": dict(results)},
    )


async def _run(check: DependencyCheck, limit_seconds: float) -> tuple[str, dict[str, Any]]:
    """Run one check. Expected dependency failures become a result; anything else propagates."""
    try:
        async with asyncio.timeout(limit_seconds):
            await check.check()
    except (DependencyUnavailableError, TimeoutError) as exc:
        # Report the driver error behind an unavailable dependency; a timeout is its own cause.
        cause = exc.__cause__ if isinstance(exc, DependencyUnavailableError) else None
        error_type = type(cause or exc).__qualname__
        _log.warning(
            "dependency check failed",
            exc_info=exc,
            extra={
                "operation": "readiness_check",
                "dependency": check.name,
                "error_type": error_type,
            },
        )
        return check.name, {"status": "unavailable", "error_type": error_type}
    return check.name, {"status": "ok"}
