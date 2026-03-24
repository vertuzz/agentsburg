"""
CLI entry point for tick processing.

Called by the cron job every minute:
    python -m backend.economy.cli

Creates its own DB session and Redis connection (independent of the
FastAPI app), loads settings from the environment, and runs one tick cycle.

This is designed to be called from outside the running FastAPI process —
the cron job runs it as a separate Python process that connects to the
same DB and Redis.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)

logger = logging.getLogger(__name__)


async def run_tick() -> None:
    """
    Run one tick cycle from the CLI.

    Sets up its own DB session, Redis connection, and clock, then
    delegates to the tick orchestrator.
    """
    # Load config — config dir can be overridden via CONFIG_DIR env var
    config_dir = Path(os.environ.get("CONFIG_DIR", "/app/config"))
    from backend.config import load_settings

    settings = load_settings(config_dir)

    # Create clock — always RealClock in CLI context
    from backend.clock import RealClock

    clock = RealClock()

    # Create database engine and session
    from backend.database import create_engine, create_sessionmaker

    engine = create_engine(settings.database)
    session_factory = create_sessionmaker(engine)

    # Create Redis connection
    from backend.redis import close_redis, create_redis

    redis_client = await create_redis(settings.redis.url)

    try:
        async with session_factory() as db:
            from backend.economy.tick import run_tick as _run_tick

            result = await _run_tick(db, redis_client, clock, settings)
            logger.info("Tick completed: %s", result)
    except Exception:
        logger.exception("Tick failed")
        sys.exit(1)
    finally:
        await close_redis(redis_client)
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run_tick())
