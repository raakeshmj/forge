"""Run the API server: `python -m forge.api`. This is the composition root."""

from collections.abc import Awaitable, Callable

import uvicorn

from forge.api.app import create_app
from forge.config import ApiSettings
from forge.infrastructure import postgres, redis
from forge.infrastructure.clock import SystemClock
from forge.infrastructure.store import PostgresUnitOfWork
from forge.observability.logs import configure_logging
from forge.observability.tracing import configure_tracing


def main() -> None:
    settings = ApiSettings.from_env()
    configure_logging(settings.log_level)
    tracer_provider = configure_tracing(settings.otel_exporter, service_name="forge-api")

    engine = postgres.create_engine(settings.database_url)
    redis_client = redis.create_client(settings.redis_url)
    shutdown: list[Callable[[], Awaitable[None]]] = [redis_client.aclose, engine.dispose]
    if tracer_provider is not None:
        shutdown.append(_async(tracer_provider.shutdown))
    app = create_app(
        uow_factory=lambda: PostgresUnitOfWork(engine),
        clock=SystemClock(),
        readiness_checks=(postgres.PostgresCheck(engine), redis.RedisCheck(redis_client)),
        readiness_timeout_seconds=settings.readiness_timeout_seconds,
        on_shutdown=shutdown,
        operator_tokens=settings.operator_tokens,
    )

    uvicorn.run(
        app,
        host=settings.api_host,
        port=settings.api_port,
        log_config=None,  # keep the JSON handler installed by configure_logging
        access_log=False,  # requests are logged by the correlation middleware instead
    )


def _async(release: Callable[[], None]) -> Callable[[], Awaitable[None]]:
    async def run() -> None:
        release()

    return run


if __name__ == "__main__":
    main()
