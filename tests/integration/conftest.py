import pytest

from forge.config import Settings


@pytest.fixture(scope="session")
def settings() -> Settings:
    """Integration tests fail loudly, never skip, when the services are not configured."""
    return Settings.from_env()
