from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from forge.config import WorkerSettings
from forge.infrastructure import postgres
from forge.infrastructure.store import PostgresUnitOfWork
from tests.databases import migrated_template, scratch_database


@pytest.fixture(scope="session")
def server_url() -> str:
    """Integration tests fail loudly, never skip, when PostgreSQL is not configured."""
    return WorkerSettings.from_env().database_url


@pytest.fixture(scope="session")
def template_database(server_url: str) -> Iterator[str]:
    with migrated_template(server_url) as name:
        yield name


@pytest.fixture
def database_url(server_url: str, template_database: str) -> Iterator[str]:
    """A fresh, fully migrated database per test, so no test can claim another's work."""
    with scratch_database(server_url, template=template_database) as url:
        yield url


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = postgres.create_engine(database_url)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def uow(engine: AsyncEngine) -> "UowFactory":
    return UowFactory(engine)


class UowFactory:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    def __call__(self) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(self.engine)
