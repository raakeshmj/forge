"""Shared helpers for migration tests."""

from pathlib import Path

from alembic.config import Config

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def alembic_config(database_url: str | None = None) -> Config:
    config = Config(str(ALEMBIC_INI))
    if database_url is not None:
        config.attributes["database_url"] = database_url
    return config
