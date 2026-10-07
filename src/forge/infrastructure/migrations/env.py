"""Alembic environment. Migrations are hand-written; autogenerate is not configured.

The URL comes from `config.attributes["database_url"]` when invoked programmatically
(tests), otherwise from FORGE_DATABASE_URL.
"""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def _database_url() -> str:
    url = config.attributes.get("database_url") or os.environ.get("FORGE_DATABASE_URL")
    if not url:
        raise RuntimeError("FORGE_DATABASE_URL is required to run migrations")
    return str(url)


def run_migrations_offline() -> None:
    context.configure(url=_database_url(), literal_binds=True, transaction_per_migration=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
