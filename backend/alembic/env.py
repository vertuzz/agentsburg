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


def get_url() -> str:
    """
    Resolve the database URL from environment or alembic.ini.

    Environment variable DATABASE_URL takes precedence, allowing CI/CD
    and Docker to override without touching config files.
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
