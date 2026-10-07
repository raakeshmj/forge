"""Throwaway PostgreSQL databases for integration tests. Never touches the developer's database."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from alembic import command
from sqlalchemy import create_engine, make_url, text

from tests.migrations import alembic_config


@contextmanager
def scratch_database(server_url: str, *, template: str | None = None) -> Iterator[str]:
    """Create an empty (or template-cloned) database; yield its URL; drop it afterwards."""
    admin_url = make_url(server_url)
    name = f"forge_test_{uuid.uuid4().hex[:12]}"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    clone = f' TEMPLATE "{template}"' if template else ""
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"{clone}'))
        yield admin_url.set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@contextmanager
def migrated_template(server_url: str) -> Iterator[str]:
    """A database migrated to head, usable as a CREATE DATABASE template. Yields its name."""
    with scratch_database(server_url) as url:
        command.upgrade(alembic_config(url), "head")
        yield str(make_url(url).database)
