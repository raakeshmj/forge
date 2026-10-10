"""`python -m forge.benchmarking`: run every arm on a fresh database and write the results.

The database server comes from FORGE_DATABASE_URL; a scratch database is created on it,
migrated, used, and dropped. The developer's own database is never touched.
"""

import argparse
import asyncio
import logging
import platform
import subprocess
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, make_url, text

from forge.benchmarking.harness import Config, Harness
from forge.benchmarking.report import to_json, to_markdown
from forge.config import WorkerSettings
from forge.infrastructure import postgres
from forge.infrastructure.clock import SystemClock
from forge.infrastructure.store import PostgresUnitOfWork
from forge.observability.logs import configure_logging


@contextmanager
def scratch_database(server_url: str, alembic_ini: Path) -> Iterator[str]:
    admin_url = make_url(server_url)
    name = f"forge_bench_{uuid.uuid4().hex[:10]}"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
        url = admin_url.set(database=name).render_as_string(hide_password=False)
        config = AlembicConfig(str(alembic_ini))
        config.attributes["database_url"] = url
        command.upgrade(config, "head")
        yield url
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def environment(database_url: str) -> dict[str, Any]:
    try:
        commit = subprocess.run(
            ["git", "describe", "--always", "--dirty"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "unknown"
    engine = create_engine(make_url(database_url))
    with engine.connect() as connection:
        server = str(connection.scalar(text("SHOW server_version")))
    engine.dispose()
    return {
        "commit": commit,
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "python": f"Python {sys.version.split()[0]}",
        "platform": f"{platform.system()} {platform.machine()}",
        "postgres": f"PostgreSQL {server}",
    }


async def run(database_url: str, config: Config) -> Any:
    engine = postgres.create_engine(database_url)
    try:
        return await Harness(lambda: PostgresUnitOfWork(engine), SystemClock(), config).run()
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    defaults = Config()
    parser.add_argument("--repeats", type=int, default=defaults.repeats)
    parser.add_argument("--seed", type=int, default=defaults.seed)
    parser.add_argument("--json", type=Path, default=Path("benchmarks/results/latest.json"))
    parser.add_argument("--markdown", type=Path, default=Path("docs/BENCHMARKS.md"))
    parser.add_argument("--alembic-ini", type=Path, default=Path("alembic.ini"))
    args = parser.parse_args()
    settings = WorkerSettings.from_env()
    configure_logging("WARNING")
    logging.getLogger("forge.benchmarking").setLevel(logging.INFO)
    config = Config(repeats=args.repeats, seed=args.seed)
    with scratch_database(settings.database_url, args.alembic_ini) as url:
        env = environment(url)
        results = asyncio.run(run(url, config))
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(to_json(results, env))
    args.markdown.write_text(to_markdown(results, env))
    print(f"wrote {args.json} and {args.markdown}")  # noqa: T201 - CLI output


if __name__ == "__main__":
    main()
