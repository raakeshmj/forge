"""Redis access: ephemeral coordination, never authoritative state (ARCHITECTURE.md §20)."""

from redis.asyncio import Redis
from redis.exceptions import RedisError

from forge.health import DependencyUnavailableError


def create_client(redis_url: str) -> Redis:
    """Create the process-wide client. Connections are opened lazily."""
    return Redis.from_url(redis_url)


class RedisCheck:
    name = "redis"

    def __init__(self, client: Redis) -> None:
        self._client = client

    async def check(self) -> None:
        try:
            await self._client.ping()
        except (RedisError, OSError) as exc:
            raise DependencyUnavailableError(self.name) from exc
