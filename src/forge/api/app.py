"""FastAPI application factory."""

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from forge.api import (
    agents,
    capabilities,
    evaluations,
    governance,
    health,
    insights,
    learning,
    missions,
    tools,
)
from forge.api.correlation import correlation_middleware
from forge.domain.common import DomainError
from forge.domain.repositories import (
    AlreadyExistsError,
    Clock,
    ExecutionWakeup,
    NotFoundError,
    NoWakeup,
    UnitOfWorkFactory,
)
from forge.domain.skill import LearningError
from forge.evaluation.service import MissionNotFinishedError
from forge.health import DependencyCheck
from forge.orchestration.missions import MissionAlreadyFinishedError
from forge.security.approvals import ApprovalError


def create_app(
    *,
    uow_factory: UnitOfWorkFactory,
    clock: Clock,
    readiness_checks: Sequence[DependencyCheck],
    readiness_timeout_seconds: float,
    on_shutdown: Sequence[Callable[[], Awaitable[None]]] = (),
    operator_tokens: Mapping[str, str] | None = None,
    wakeup: ExecutionWakeup | None = None,
    stream_poll_seconds: float = 1.0,
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
    app.state.operator_tokens = dict(operator_tokens or {})  # none configured: nobody is one
    app.state.wakeup = wakeup or NoWakeup()  # external events for the execution backend
    app.state.stream_poll_seconds = stream_poll_seconds  # live event feed cadence
    app.middleware("http")(correlation_middleware)
    app.include_router(health.router)
    app.include_router(agents.router)
    app.include_router(missions.router)
    app.include_router(tools.router)
    app.include_router(evaluations.router)
    app.include_router(capabilities.router)
    app.include_router(learning.router)
    app.include_router(governance.router)
    app.include_router(insights.router)
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
    app.add_exception_handler(MissionNotFinishedError, handler(status.HTTP_409_CONFLICT))
    app.add_exception_handler(LearningError, handler(status.HTTP_409_CONFLICT))
    app.add_exception_handler(ApprovalError, handler(status.HTTP_409_CONFLICT))
    app.add_exception_handler(DomainError, handler(status.HTTP_422_UNPROCESSABLE_CONTENT))
