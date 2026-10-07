"""The query-building table definitions must match what the migrations actually create."""

import pytest
from sqlalchemy import create_engine, inspect

from forge.infrastructure.tables import metadata

pytestmark = pytest.mark.integration


def test_table_definitions_match_migrated_schema(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        for table in metadata.sorted_tables:
            migrated = {column["name"]: column for column in inspector.get_columns(table.name)}
            declared = {column.name: column for column in table.columns}
            assert set(declared) == set(migrated), table.name
            for name, column in declared.items():
                assert column.nullable == migrated[name]["nullable"], f"{table.name}.{name}"
    finally:
        engine.dispose()
