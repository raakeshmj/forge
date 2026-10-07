"""FastAPI application factory."""

from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager

from fastapi import FastAPI

from forge.api import health
from forge.api.correlation import correlation_middleware
from forge.health import DependencyCheck


def create_app(
    *,
    readiness_checks: Sequence[DependencyCheck],
    readiness_timeout_seconds: float,
    on_shutdown: Sequence[Callable[[], Awaitable[None]]] = (),
) -> FastAPI:
    """Build the app from already-constructed dependencies; `on_shutdown` releases them."""

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        for release in on_shutdown:
            await release()

    app = FastAPI(title="FORGE", version="0.0.0", lifespan=lifespan)
    app.state.readiness_checks = tuple(readiness_checks)
    app.state.readiness_timeout_seconds = readiness_timeout_seconds
    app.middleware("http")(correlation_middleware)
    app.include_router(health.router)
    return app
