from alembic.script import ScriptDirectory

from tests.migrations import alembic_config


def test_migration_history_has_exactly_one_head() -> None:
    """Two heads means concurrent migrations were merged without being linearised."""
    heads = ScriptDirectory.from_config(alembic_config()).get_heads()

    assert len(heads) == 1, f"expected a single migration head, found {heads}"
