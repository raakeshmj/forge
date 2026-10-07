import pytest

from forge.config import ApiSettings
from forge.infrastructure import postgres, redis

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


@pytest.fixture(scope="module")
def api_settings() -> ApiSettings:
    return ApiSettings.from_env()


async def test_postgres_check_passes_against_running_postgres(api_settings: ApiSettings) -> None:
    engine = postgres.create_engine(api_settings.database_url)
    try:
        await postgres.PostgresCheck(engine).check()
    finally:
        await engine.dispose()


async def test_redis_check_passes_against_running_redis(api_settings: ApiSettings) -> None:
    client = redis.create_client(api_settings.redis_url)
    try:
        await redis.RedisCheck(client).check()
    finally:
        await client.aclose()
