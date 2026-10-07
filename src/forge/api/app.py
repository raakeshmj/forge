"""FastAPI application factory."""

from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from forge.api import agents, health, missions
from forge.api.correlation import correlation_middleware
from forge.domain.common import DomainError
from forge.domain.repositories import AlreadyExistsError, Clock, NotFoundError, UnitOfWorkFactory
from forge.health import DependencyCheck
from forge.orchestration.missions import MissionAlreadyFinishedError


def create_app(
    *,
    uow_factory: UnitOfWorkFactory,
    clock: Clock,
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

    app = FastAPI(title="FORGE", version="0.1.0", lifespan=lifespan)
    app.state.uow_factory = uow_factory
    app.state.clock = clock
    app.state.readiness_checks = tuple(readiness_checks)
    app.state.readiness_timeout_seconds = readiness_timeout_seconds
    app.middleware("http")(correlation_middleware)
    app.include_router(health.router)
    app.include_router(agents.router)
    app.include_router(missions.router)
    _register_error_handlers(app)
    return app


def _register_error_handlers(app: FastAPI) -> None:
    """Map expected domain outcomes to HTTP statuses. Anything else is a 500 and is logged."""

    def handler(code: int) -> Callable[[Request, Exception], Awaitable[JSONResponse]]:
        async def respond(_: Request, exc: Exception) -> JSONResponse:
            return JSONResponse(status_code=code, content={"detail": str(exc)})

        return respond

    app.add_exception_handler(NotFoundError, handler(status.HTTP_404_NOT_FOUND))
    app.add_exception_handler(AlreadyExistsError, handler(status.HTTP_409_CONFLICT))
    app.add_exception_handler(MissionAlreadyFinishedError, handler(status.HTTP_409_CONFLICT))
    app.add_exception_handler(DomainError, handler(status.HTTP_422_UNPROCESSABLE_CONTENT))
