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
import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import sentry_sdk
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from backend.clock import Clock, RealClock
from backend.config import Settings, load_settings
from backend.database import create_engine, create_sessionmaker
from backend.redis import close_redis, create_redis

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

# Initialize Sentry only when SENTRY_DSN is set (production/staging).
# Tests and local dev won't have this env var, so Sentry stays disabled.
_sentry_dsn = os.environ.get("SENTRY_DSN")
if _sentry_dsn:
    sentry_sdk.init(
        dsn=_sentry_dsn,
        send_default_pii=True,
        enable_logs=True,
        traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
    )


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

    logger.info("Starting Agent Economy backend")
    logger.info("Database configured")
    logger.info("Redis configured")

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
        seed_central_bank,
        seed_goods,
        seed_government,
        seed_npc_businesses,
        seed_recipes,
        seed_zones,
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
        title="Agentsburg",
        description="An arena where AI agents compete in a simulated city economy",
        version="0.1.0",
        docs_url="/docs" if settings.server.debug else None,
        redoc_url="/redoc" if settings.server.debug else None,
        lifespan=lifespan,
    )

    # Store pre-lifespan state that lifespan init needs
    app.state.settings = settings
    app.state.clock = clock
    app.state.rate_limit_enabled = True  # Can be disabled for tests

    # CORS — allow frontend dev server and production origins.
    # In Docker, nginx proxies all requests so the browser only talks to nginx
    # (port 80). We also allow localhost:5173 for local dev without Docker.
    base = settings.server.base_url.rstrip("/")
    # Derive www variant for production domains
    from urllib.parse import urlparse

    parsed = urlparse(base)
    www_origin = (
        f"{parsed.scheme}://www.{parsed.hostname}"
        if parsed.hostname and not parsed.hostname.startswith("www.")
        else None
    )

    prod_origins = [base]
    if www_origin:
        prod_origins.append(www_origin)
    prod_origins += [
        "http://localhost",
        "http://localhost:80",
        "http://localhost:5173",
        "http://127.0.0.1",
        "http://127.0.0.1:80",
    ]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://localhost:3000",
            "http://localhost:8000",
        ]
        if settings.server.debug
        else prod_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # --- Routes ---

    @app.get("/health", tags=["infrastructure"])
    async def health() -> dict[str, str]:
        """Health check endpoint. Returns 200 when the service is running."""
        return {"status": "ok"}

    from backend.rest.router import register_error_handlers
    from backend.rest.router import router as rest_router

    app.include_router(rest_router)
    register_error_handlers(app)

    # REST API router — dashboard endpoints (Phase 9)
    from backend.api.router import router as api_router

    app.include_router(api_router, prefix="/api")

    # Admin endpoint — trigger economy tick processing (dev/test only)
    @app.post("/admin/tick", tags=["infrastructure"])
    async def trigger_tick(authorization: str = Header(default=None)):
        """Manually trigger a fast tick. Requires ADMIN_TOKEN."""
        admin_token = os.environ.get("ADMIN_TOKEN")
        if not admin_token:
            raise HTTPException(status_code=503, detail="Admin endpoint not configured")
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing or invalid authorization header")
        if authorization[7:] != admin_token:
            raise HTTPException(status_code=401, detail="Invalid admin token")

        from backend.economy.fast_tick import run_fast_tick

        async with app.state.session_factory() as db:
            result = await run_fast_tick(db, app.state.clock, app.state.settings)
            await db.commit()
        return result

    return app


# --- Module-level app instance for uvicorn ---
# uvicorn backend.main:app
app = create_app()
