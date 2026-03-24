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
- Real REST API routing and validation
- Real tool dispatch

If a test passes here, a real AI agent doing the same calls will get the same result.

Environment is loaded automatically from .env.test (in the backend/ directory).
No manual env var exports are needed.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from backend.clock import MockClock
from backend.config import Settings, load_settings
from backend.main import create_app
from backend.models.agent import Agent
from backend.models.base import Base
from backend.models.inventory import InventoryItem

# ---------------------------------------------------------------------------
# Load .env.test at import time so all settings pick up test values.
# We resolve the path relative to this file's location so it works
# regardless of the working directory pytest is invoked from.
# ---------------------------------------------------------------------------

_BACKEND_DIR = Path(__file__).parent.parent  # .../backend/
_ENV_TEST_FILE = _BACKEND_DIR / ".env.test"


def _load_env_file(path: Path) -> None:
    """
    Parse a .env file and set values into os.environ.

    Real environment variables already present take priority and are
    NOT overwritten — same semantics as pydantic-settings.
    """
    if not path.exists():
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # Strip optional surrounding quotes
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            # Real env vars take priority
            if key not in os.environ:
                os.environ[key] = value


# Apply test environment before any test code runs
_load_env_file(_ENV_TEST_FILE)

# ---------------------------------------------------------------------------
# Config directory — locate the YAML config files
# ---------------------------------------------------------------------------

CONFIG_DIR = _BACKEND_DIR.parent / "config"

if not CONFIG_DIR.exists():
    # Try one level up (monorepo layouts vary)
    CONFIG_DIR = _BACKEND_DIR.parent.parent / "config"


# ---------------------------------------------------------------------------
# Settings helpers — read from os.environ (already populated above)
# ---------------------------------------------------------------------------


def _get_test_db_url() -> str:
    return os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/agent_economy_test",
    )


def _get_test_redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://localhost:6379/1")


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
    # Override DB and Redis with test URLs (already set via .env.test but
    # we apply them explicitly here so the fixture is self-documenting)
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
    return MockClock(start=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC))


@pytest_asyncio.fixture
async def app(settings: Settings, clock: MockClock, create_test_database):
    """
    Create a test FastAPI app with:
    - MockClock (the only mock)
    - Real test database
    - Real test Redis
    """
    test_app = create_app(settings=settings, clock=clock)
    test_app.state.rate_limit_enabled = False  # Disable rate limiting in tests

    # Start the lifespan manually
    async with test_app.router.lifespan_context(test_app):
        yield test_app


@pytest_asyncio.fixture
async def client(app) -> AsyncGenerator[httpx.AsyncClient]:
    """
    Async HTTP client using ASGI transport.

    Full middleware stack executes — no shortcuts.
    Real HTTP requests to the REST API endpoints.
    """
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as c:
        yield c


@pytest_asyncio.fixture
async def db(app) -> AsyncGenerator[AsyncSession]:
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


# ---------------------------------------------------------------------------
# Shared test helpers — used by multiple test files
# ---------------------------------------------------------------------------


async def give_balance(app, agent_name: str, amount: float) -> None:
    """Directly set an agent's balance for test setup."""
    async with app.state.session_factory() as session:
        result = await session.execute(select(Agent).where(Agent.name == agent_name))
        agent = result.scalar_one()
        agent.balance = Decimal(str(amount))
        await session.commit()


async def get_balance(app, agent_name: str) -> Decimal:
    """Read an agent's current balance."""
    async with app.state.session_factory() as session:
        result = await session.execute(select(Agent).where(Agent.name == agent_name))
        agent = result.scalar_one()
        return Decimal(str(agent.balance))


async def force_agent_age(app, agent_name: str, age_seconds: int) -> None:
    """Set an agent's created_at to make them appear old enough."""
    clock = app.state.clock
    now = clock.now()
    async with app.state.session_factory() as session:
        result = await session.execute(select(Agent).where(Agent.name == agent_name))
        agent = result.scalar_one()
        agent.created_at = now - timedelta(seconds=age_seconds)
        await session.commit()


async def jail_agent(app, agent_name: str, clock, hours: float = 2.0) -> None:
    """Put an agent in jail for the given number of hours."""
    now = clock.now()
    async with app.state.session_factory() as session:
        result = await session.execute(select(Agent).where(Agent.name == agent_name))
        agent = result.scalar_one()
        agent.jail_until = now + timedelta(hours=hours)
        await session.commit()


async def give_inventory(app, agent_name: str, good_slug: str, quantity: int) -> None:
    """Directly give an agent inventory for test setup."""
    async with app.state.session_factory() as session:
        result = await session.execute(select(Agent).where(Agent.name == agent_name))
        agent = result.scalar_one()

        inv_result = await session.execute(
            select(InventoryItem).where(
                InventoryItem.owner_type == "agent",
                InventoryItem.owner_id == agent.id,
                InventoryItem.good_slug == good_slug,
            )
        )
        inv_item = inv_result.scalar_one_or_none()
        if inv_item:
            inv_item.quantity = quantity
        else:
            session.add(
                InventoryItem(
                    owner_type="agent",
                    owner_id=agent.id,
                    good_slug=good_slug,
                    quantity=quantity,
                )
            )
        await session.commit()


async def deactivate_agent(app, agent_name: str) -> None:
    """Directly deactivate an agent for test setup."""
    async with app.state.session_factory() as session:
        result = await session.execute(select(Agent).where(Agent.name == agent_name))
        agent = result.scalar_one()
        agent.is_active = False
        await session.commit()


async def get_agent_field(app, agent_name: str, field: str):
    """Read an arbitrary field from an agent record."""
    async with app.state.session_factory() as session:
        result = await session.execute(select(Agent).where(Agent.name == agent_name))
        agent = result.scalar_one()
        return getattr(agent, field)


async def get_inventory_qty(app, agent_name: str, good_slug: str) -> int:
    """Read an agent's inventory quantity for a given good."""
    async with app.state.session_factory() as session:
        result = await session.execute(select(Agent).where(Agent.name == agent_name))
        agent = result.scalar_one()
        inv_result = await session.execute(
            select(InventoryItem).where(
                InventoryItem.owner_type == "agent",
                InventoryItem.owner_id == agent.id,
                InventoryItem.good_slug == good_slug,
            )
        )
        inv_item = inv_result.scalar_one_or_none()
        return inv_item.quantity if inv_item else 0
