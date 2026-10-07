"""Run the full migration history against a throwaway database, never the developer's."""

import uuid
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, make_url, text

from forge.config import Settings
from tests.migrations import alembic_config

pytestmark = pytest.mark.integration


@pytest.fixture
def scratch_database_url(settings: Settings) -> Iterator[str]:
    admin_url = make_url(settings.database_url)
    name = f"forge_migration_test_{uuid.uuid4().hex[:12]}"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
        yield admin_url.set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def _current_revision(database_url: str) -> str | None:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            exists = connection.execute(text("SELECT to_regclass('alembic_version')")).scalar()
            if exists is None:
                return None
            return connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    finally:
        engine.dispose()


def test_upgrade_downgrade_upgrade_round_trip(scratch_database_url: str) -> None:
    config = alembic_config(scratch_database_url)
    head = ScriptDirectory.from_config(config).get_current_head()

    command.upgrade(config, "head")
    assert _current_revision(scratch_database_url) == head

    command.downgrade(config, "base")
    assert _current_revision(scratch_database_url) is None

    command.upgrade(config, "head")
    assert _current_revision(scratch_database_url) == head
