"""
CLI entry point for data maintenance.

Called by the maintenance Docker service every 6 hours:
    python -m backend.economy.maintenance_cli

Creates its own DB session and clock (independent of the FastAPI app),
loads settings from the environment, and runs the full maintenance cycle:
  - Downsample raw MarketTrades → hourly PriceAggregates
  - Downsample hourly PriceAggregates → daily PriceAggregates
  - Delete raw trades older than 48h
  - Delete transactions older than 7 days
  - Take an EconomySnapshot
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


async def run_maintenance() -> None:
    """
    Run one full maintenance cycle from the CLI.

    Sets up its own DB session and clock, delegates to downsample_data(),
    commits the transaction, and exits.
    """
    config_dir = Path(os.environ.get("CONFIG_DIR", "/app/config"))
    from backend.config import load_settings

    settings = load_settings(config_dir)

    from backend.clock import RealClock

    clock = RealClock()

    from backend.database import create_engine, create_sessionmaker

    engine = create_engine(settings.database)
    session_factory = create_sessionmaker(engine)

    try:
        async with session_factory() as db:
            from backend.economy.maintenance import downsample_data

            logger.info("Starting maintenance cycle at %s", clock.now().isoformat())
            result = await downsample_data(db, clock)
            await db.commit()
            logger.info("Maintenance cycle complete: %s", result)
    except Exception:
        logger.exception("Maintenance cycle failed")
        sys.exit(1)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run_maintenance())
