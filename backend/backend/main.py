"""
FastAPI application factory for Agent Economy.

The app is created via create_app() to support:
- Different configs for production vs testing
- Testable lifespan (MockClock injection, test DB, etc.)

Lifespan handles:
1. Loading YAML config into app.state.settings
2. Creating async SQLAlchemy engine + sessionmaker
3. Connecting to Redis
4. Seeding reference data (zones) from YAML config
5. Storing Clock instance on app.state
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.clock import Clock, RealClock
from backend.config import Settings, load_settings
from backend.database import create_engine, create_sessionmaker
from backend.redis import close_redis, create_redis

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan: set up shared resources, yield, then tear down.

    Resources stored on app.state:
    - settings: frozen Settings object
    - engine: AsyncEngine (SQLAlchemy)
    - session_factory: async_sessionmaker
    - redis: aioredis.Redis client
    - clock: Clock instance (RealClock in production, MockClock in tests)
    """
    settings: Settings = app.state.settings
    clock: Clock = app.state.clock

    logger.info("Starting Agent Economy backend")
    logger.info("Database: %s", settings.database.url.split("@")[-1])  # hide credentials
    logger.info("Redis: %s", settings.redis.url)

    # --- Database setup ---
    engine = create_engine(settings.database)
    session_factory = create_sessionmaker(engine)
    app.state.engine = engine
    app.state.session_factory = session_factory
    logger.info("Database engine created")

    # --- Redis setup ---
    redis_client = await create_redis(settings.redis.url)
    app.state.redis = redis_client
    logger.info("Redis connected")

    # --- Seed reference data ---
    from backend.economy.bootstrap import (
        seed_zones,
        seed_goods,
        seed_recipes,
        seed_central_bank,
        seed_government,
        seed_npc_businesses,
    )

    async with session_factory() as db:
        try:
            await seed_zones(db, settings)
            await seed_goods(db, settings)
            await seed_recipes(db, settings)
            await seed_central_bank(db, settings)
            await seed_government(db, settings)
            await seed_npc_businesses(db, settings)
            await db.commit()
            logger.info("Bootstrap seeding complete")
        except Exception:
            await db.rollback()
            logger.exception("Bootstrap seeding failed — continuing anyway")

    yield

    # --- Teardown ---
    logger.info("Shutting down Agent Economy backend")
    await close_redis(redis_client)
    await engine.dispose()
    logger.info("Shutdown complete")


def create_app(
    settings: Settings | None = None,
    clock: Clock | None = None,
    config_dir: str | Path | None = None,
) -> FastAPI:
    """
    Create and configure the FastAPI application.

    Args:
        settings: Pre-built Settings object. If None, loads from YAML + env.
        clock: Clock implementation. Defaults to RealClock. Pass MockClock in tests.
        config_dir: Directory to load YAML configs from. Defaults to /app/config.

    Returns:
        Configured FastAPI app (lifespan not yet started).
    """
    if settings is None:
        settings = load_settings(config_dir)
    if clock is None:
        clock = RealClock()

    app = FastAPI(
        title="Agent Economy",
        description="Real-time multiplayer economic simulator for AI agents",
        version="0.1.0",
        docs_url="/docs" if settings.server.debug else None,
        redoc_url="/redoc" if settings.server.debug else None,
        lifespan=lifespan,
    )

    # Store pre-lifespan state that lifespan init needs
    app.state.settings = settings
    app.state.clock = clock

    # CORS — allow frontend dev server and any configured origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.server.debug else ["http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # --- Routes ---

    @app.get("/health", tags=["infrastructure"])
    async def health() -> dict[str, str]:
        """Health check endpoint. Returns 200 when the service is running."""
        return {"status": "ok"}

    # MCP endpoint — all agent interactions flow through here
    from backend.mcp.router import router as mcp_router

    app.include_router(mcp_router)

    # REST API router — dashboard endpoints (Phase 9)
    # from backend.api.router import router as api_router
    # app.include_router(api_router, prefix="/api")

    return app


# --- Module-level app instance for uvicorn ---
# uvicorn backend.main:app
app = create_app()
