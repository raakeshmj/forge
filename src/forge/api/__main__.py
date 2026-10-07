"""Run the API server: `python -m forge.api`. This is the composition root."""

import uvicorn

from forge.api.app import create_app
from forge.config import ApiSettings
from forge.infrastructure import postgres, redis
from forge.observability.logs import configure_logging


def main() -> None:
    settings = ApiSettings.from_env()
    configure_logging(settings.log_level)

    engine = postgres.create_engine(settings.database_url)
    redis_client = redis.create_client(settings.redis_url)
    app = create_app(
        readiness_checks=(postgres.PostgresCheck(engine), redis.RedisCheck(redis_client)),
        readiness_timeout_seconds=settings.readiness_timeout_seconds,
        on_shutdown=(redis_client.aclose, engine.dispose),
    )

    uvicorn.run(
        app,
        host=settings.api_host,
        port=settings.api_port,
        log_config=None,  # keep the JSON handler installed by configure_logging
        access_log=False,  # requests are logged by the correlation middleware instead
    )


if __name__ == "__main__":
    main()
