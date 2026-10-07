"""Dependency checks against a closed local port: failures must surface as DependencyUnavailable."""

import pytest

from forge.health import DependencyUnavailableError
from forge.infrastructure import postgres, redis

pytestmark = pytest.mark.anyio

# Port 1 on loopback is reserved (tcpmux) and refuses connections on any dev or CI machine.
CLOSED = "127.0.0.1:1"


async def test_unreachable_postgres_is_reported_unavailable() -> None:
    engine = postgres.create_engine(f"postgresql+psycopg://forge:forge@{CLOSED}/forge")
    try:
        with pytest.raises(DependencyUnavailableError) as excinfo:
            await postgres.PostgresCheck(engine).check()
    finally:
        await engine.dispose()

    assert excinfo.value.dependency == "postgres"
    assert excinfo.value.__cause__ is not None


async def test_unreachable_redis_is_reported_unavailable() -> None:
    client = redis.create_client(f"redis://{CLOSED}/0")
    try:
        with pytest.raises(DependencyUnavailableError) as excinfo:
            await redis.RedisCheck(client).check()
    finally:
        await client.aclose()

    assert excinfo.value.dependency == "redis"
    assert excinfo.value.__cause__ is not None
