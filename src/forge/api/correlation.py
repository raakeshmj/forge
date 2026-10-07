"""Correlation id propagation and request logging for HTTP requests."""

import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response

from forge.observability.logs import correlation_id

CORRELATION_HEADER = "X-Correlation-ID"

_log = logging.getLogger("forge.api.request")

# Client-supplied ids end up in logs; accept only a bounded, log-safe charset.
_VALID_CORRELATION_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")


async def correlation_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Adopt a valid inbound correlation id or mint one, echo it, and log the request.

    Only the path is logged; query strings can carry credentials.
    """
    inbound = request.headers.get(CORRELATION_HEADER)
    if inbound is not None and _VALID_CORRELATION_ID.fullmatch(inbound):
        value = inbound
    else:
        value = str(uuid.uuid4())
    token = correlation_id.set(value)
    started = time.perf_counter()
    try:
        response = await call_next(request)
        _log.info(
            "request completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
    finally:
        correlation_id.reset(token)
    response.headers[CORRELATION_HEADER] = value
    return response
