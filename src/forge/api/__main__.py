"""Run the API server: `python -m forge.api`."""

import uvicorn

from forge.api.app import create_app
from forge.config import Settings
from forge.observability.logs import configure_logging


def main() -> None:
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    uvicorn.run(
        create_app(),
        host=settings.api_host,
        port=settings.api_port,
        log_config=None,  # keep the JSON handler installed by configure_logging
        access_log=False,  # requests are logged by the correlation middleware instead
    )


if __name__ == "__main__":
    main()
