"""
Alembic async migration environment for Agent Economy.

Uses asyncpg driver with SQLAlchemy's async engine.
Imports all models so their metadata is available for autogenerate.
"""

from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

# Ensure the backend package is importable when alembic runs from the
# backend/ directory (where alembic.ini lives)
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import all models so their metadata is registered on Base
# This must happen before we reference Base.metadata below
import backend.models  # noqa: F401  (registers all model tables)
from backend.models.base import Base

# this is the Alembic Config object, which provides access to the .ini file
config = context.config

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Metadata for 'autogenerate' support
target_metadata = Base.metadata


def _load_dotenv() -> None:
    """
    Load .env file from the backend/ directory if DATABASE_URL is not already
    set in the environment.  This makes `uv run alembic upgrade head` work
    without requiring the caller to export DATABASE_URL manually.
    """
    if "DATABASE_URL" in os.environ:
        return
    dotenv_path = Path(__file__).parent.parent / ".env"
    if not dotenv_path.exists():
        return
    with open(dotenv_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            if key not in os.environ:
                os.environ[key] = value


_load_dotenv()


def get_url() -> str:
    """
    Resolve the database URL from environment or alembic.ini.

    Environment variable DATABASE_URL takes precedence, allowing CI/CD
    and Docker to override without touching config files.
    Falls back to .env file in the backend/ directory (loaded above).
    """
    return os.environ.get(
        "DATABASE_URL",
        config.get_main_option("sqlalchemy.url",
                               "postgresql+asyncpg://postgres:postgres@localhost:5432/agent_economy"),
    )


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode.

    This configures the context with just a URL and not an Engine.
    Calls to context.execute() emit the given string to the script output.
    """
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations using an async engine."""
    engine = create_async_engine(
        get_url(),
        poolclass=pool.NullPool,
    )

    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await engine.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode using an async engine."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
