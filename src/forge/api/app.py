"""FastAPI application factory."""

from fastapi import FastAPI

from forge.api import health
from forge.api.correlation import correlation_middleware


def create_app() -> FastAPI:
    app = FastAPI(title="FORGE", version="0.0.0")
    app.middleware("http")(correlation_middleware)
    app.include_router(health.router)
    return app
