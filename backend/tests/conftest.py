"""
Test configuration for Agent Economy simulation tests.

This module sets up the full test infrastructure:
- Real PostgreSQL test database (separate from production)
- Real Redis (using DB index 1 to isolate from production)
- Real FastAPI app with ASGI transport (full middleware stack)
- MockClock for deterministic time control
- Fixtures for DB inspection, tick running, and agent helpers

The ONLY mock is MockClock. Everything else is real:
- Real DB queries
- Real Redis TTLs (but cleared between tests)
- Real HTTP requests through the full middleware/auth/routing stack
- Real JSON-RPC protocol parsing
- Real tool dispatch

If a test passes here, a real AI agent doing the same calls will get the same result.
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.clock import MockClock
from backend.config import load_settings, Settings
from backend.database import create_engine, create_sessionmaker
from backend.main import create_app
from backend.models.base import Base

# ---------------------------------------------------------------------------
# Test database and Redis configuration
# ---------------------------------------------------------------------------

# Use a dedicated test database by appending "_test" to the DB name
# or using TEST_DATABASE_URL env var directly
def _get_test_db_url() -> str:
    test_url = os.environ.get("TEST_DATABASE_URL")
    if test_url:
        return test_url

    base_url = os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/agent_economy",
    )
    # Append _test to the database name
    # postgresql+asyncpg://user:pass@host:port/dbname -> .../dbname_test
    return re.sub(r"(/[^/]+)$", r"\1_test", base_url)


def _get_test_redis_url() -> str:
    # Use Redis DB index 1 for tests (production uses index 0)
    base_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    # Replace /0 with /1 at end of URL, or append /1
    if re.search(r"/\d+$", base_url):
        return re.sub(r"/\d+$", "/1", base_url)
    return base_url.rstrip("/") + "/1"


# ---------------------------------------------------------------------------
# Config directory
# ---------------------------------------------------------------------------

CONFIG_DIR = Path(__file__).parent.parent.parent.parent / "config"

if not CONFIG_DIR.exists():
    # Try alternative paths
    CONFIG_DIR = Path(__file__).parent.parent.parent / "config"


# ---------------------------------------------------------------------------
# Session-scoped: database setup/teardown
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(scope="session")
async def test_db_url() -> str:
    return _get_test_db_url()


@pytest_asyncio.fixture(scope="session")
async def test_redis_url() -> str:
    return _get_test_redis_url()


@pytest_asyncio.fixture(scope="session")
async def create_test_database(test_db_url: str):
    """
    Create the test database schema once per test session.

    Uses SQLAlchemy's create_all() to create tables directly from models
    (faster than running alembic migrations in tests).
    """
    engine = create_async_engine(test_db_url, echo=False)

    async with engine.begin() as conn:
        # Import all models to register them with Base.metadata
        import backend.models  # noqa: F401

        # Drop all and recreate (clean slate for each test session)
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    await engine.dispose()


# ---------------------------------------------------------------------------
# Function-scoped: per-test isolation
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def settings(test_db_url: str, test_redis_url: str) -> Settings:
    """Load settings with test DB and Redis URLs."""
    s = load_settings(CONFIG_DIR)
    # Override DB and Redis with test URLs
    from backend.config import DatabaseSettings, RedisSettings
    return Settings(
        database=DatabaseSettings(url=test_db_url, echo=False),
        redis=RedisSettings(url=test_redis_url),
        server=s.server,
        economy=s.economy,
        goods=s.goods,
        recipes=s.recipes,
        zones=s.zones,
        government=s.government,
        npc_demand=s.npc_demand,
        bootstrap=s.bootstrap,
    )


@pytest_asyncio.fixture
async def clock() -> MockClock:
    """MockClock starting at 2026-01-01 00:00:00 UTC."""
    return MockClock(start=datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc))


@pytest_asyncio.fixture
async def app(settings: Settings, clock: MockClock, create_test_database):
    """
    Create a test FastAPI app with:
    - MockClock (the only mock)
    - Real test database
    - Real test Redis
    """
    test_app = create_app(settings=settings, clock=clock)

    # Start the lifespan manually
    async with test_app.router.lifespan_context(test_app):
        yield test_app


@pytest_asyncio.fixture
async def client(app) -> AsyncGenerator[httpx.AsyncClient, None]:
    """
    Async HTTP client using ASGI transport.

    Full middleware stack executes — no shortcuts.
    Real HTTP requests to the real POST /mcp endpoint.
    """
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as c:
        yield c


@pytest_asyncio.fixture
async def db(app) -> AsyncGenerator[AsyncSession, None]:
    """
    Direct database session for test assertions.

    Use this to inspect DB state after API calls.
    Do NOT use this for agent actions — use client/TestAgent instead.
    """
    async with app.state.session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def redis_client(app):
    """
    Direct Redis client for test inspection.

    Use this to check cooldown TTLs, tick timestamps, etc.
    """
    redis = app.state.redis

    # Clear test Redis DB before each test
    await redis.flushdb()

    yield redis

    # Clean up after test too
    await redis.flushdb()


@pytest_asyncio.fixture
async def run_tick(app, clock: MockClock):
    """
    Helper fixture that runs the tick system.

    Usage:
        # Advance clock by 1 hour and run tick
        await run_tick(hours=1)

        # Just run a tick at current clock time
        await run_tick()

        # Run many ticks quickly (advance by days)
        await run_tick(days=3)
    """
    async def _run_tick(
        hours: float = 0,
        minutes: float = 0,
        seconds: float = 0,
        days: float = 0,
    ):
        """
        Optionally advance the clock, then run one tick cycle.

        For large time advances (days), this advances the clock by the full
        amount and runs ONE tick cycle (the tick system will fire all the
        appropriate slow/daily/weekly ticks based on the elapsed time).

        Returns the tick result dict.
        """
        total_seconds = days * 86400 + hours * 3600 + minutes * 60 + seconds
        if total_seconds > 0:
            clock.advance(total_seconds)

        from backend.economy.tick import run_tick as tick_fn

        async with app.state.session_factory() as session:
            result = await tick_fn(
                db=session,
                redis=app.state.redis,
                clock=clock,
                settings=app.state.settings,
            )
        return result

    async def _run_days(num_days: int, ticks_per_day: int = 4):
        """
        Run simulation for num_days, with ticks_per_day slow-tick checkpoints.

        Each checkpoint advances the clock by (24 / ticks_per_day) hours and
        runs one tick. This is much faster than running 24 hourly ticks per day
        while still exercising all tick boundary logic.

        Args:
            num_days:     Number of simulated days to run.
            ticks_per_day: How many tick cycles to run per day (default 4 = every 6h).
        """
        hours_per_tick = 24.0 / ticks_per_day
        for _ in range(num_days * ticks_per_day):
            await _run_tick(hours=hours_per_tick)

    _run_tick.days = _run_days
    return _run_tick
