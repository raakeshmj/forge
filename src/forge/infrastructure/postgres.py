"""PostgreSQL access: the system of record (docs/ARCHITECTURE.md §20)."""

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from forge.health import DependencyUnavailableError


def create_engine(database_url: str) -> AsyncEngine:
    """Create the process-wide async engine. Connections are opened lazily."""
    return create_async_engine(database_url, pool_pre_ping=True)


class PostgresCheck:
    name = "postgres"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def check(self) -> None:
        try:
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except (SQLAlchemyError, OSError) as exc:
            raise DependencyUnavailableError(self.name) from exc
