"""Run the full migration history against a throwaway database, never the developer's."""

from collections.abc import Iterator

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from tests.databases import scratch_database
from tests.migrations import alembic_config

pytestmark = pytest.mark.integration


@pytest.fixture
def empty_database_url(server_url: str) -> Iterator[str]:
    with scratch_database(server_url) as url:
        yield url


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


def test_upgrade_downgrade_upgrade_round_trip(empty_database_url: str) -> None:
    config = alembic_config(empty_database_url)
    head = ScriptDirectory.from_config(config).get_current_head()

    command.upgrade(config, "head")
    assert _current_revision(empty_database_url) == head

    command.downgrade(config, "base")
    assert _current_revision(empty_database_url) is None

    command.upgrade(config, "head")
    assert _current_revision(empty_database_url) == head


def test_each_migration_downgrades_one_step(empty_database_url: str) -> None:
    config = alembic_config(empty_database_url)
    revisions = [script.revision for script in ScriptDirectory.from_config(config).walk_revisions()]

    command.upgrade(config, "head")
    for expected_after in [*revisions[1:], None]:
        command.downgrade(config, "-1")
        assert _current_revision(empty_database_url) == expected_after
