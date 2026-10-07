import pytest

from forge.config import Settings
from forge.infrastructure import postgres, redis

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def test_postgres_check_passes_against_running_postgres(settings: Settings) -> None:
    engine = postgres.create_engine(settings.database_url)
    try:
        await postgres.PostgresCheck(engine).check()
    finally:
        await engine.dispose()


async def test_redis_check_passes_against_running_redis(settings: Settings) -> None:
    client = redis.create_client(settings.redis_url)
    try:
        await redis.RedisCheck(client).check()
    finally:
        await client.aclose()
