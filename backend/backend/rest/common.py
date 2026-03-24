"""
Shared dependencies, rate limiting, error handling, and helpers for REST routes.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from backend.database import get_db
from backend.errors import ToolError

if TYPE_CHECKING:
    import redis.asyncio as aioredis
    from sqlalchemy.ext.asyncio import AsyncSession


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


async def _resolve_agent(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Extract and validate the Bearer token from the Authorization header."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid Authorization header. Use: Authorization: Bearer <action_token>",
        )
    token = auth_header[len("Bearer ") :].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Empty token")

    from backend.agents.service import get_agent_by_action_token

    agent = await get_agent_by_action_token(db, token)
    if agent is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid token. Use signup to get a valid action_token.",
        )
    return agent


async def get_current_agent(
    agent=Depends(_resolve_agent),
):
    """Resolve agent and reject deactivated agents."""
    if agent.is_deactivated():
        raise HTTPException(
            status_code=403,
            detail=(
                "Your agent has been permanently deactivated after "
                f"{agent.bankruptcy_count} bankruptcies. "
                "You can still view your status at GET /v1/me but cannot perform other actions."
            ),
        )
    return agent


async def get_current_agent_allow_inactive(
    agent=Depends(_resolve_agent),
):
    """Resolve agent without checking active status (used by /me)."""
    return agent


def get_clock(request: Request):
    """Return the application clock (real or mock)."""
    return request.app.state.clock


def get_redis(request: Request):
    """Return the application Redis connection."""
    return request.app.state.redis


def get_settings(request: Request):
    """Return the application settings loaded from YAML config."""
    return request.app.state.settings


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


async def _check_rate_limit_bucket(
    redis: aioredis.Redis,
    key: str,
    max_requests: int,
    window_seconds: int,
) -> None:
    """Increment a Redis counter and raise HTTPException(429) if exceeded."""
    current = await redis.incr(key)
    if current == 1:
        await redis.expire(key, window_seconds)
    if current > max_requests:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Max {max_requests} requests per {window_seconds}s.",
        )


async def check_rate_limit(
    request: Request,
    redis: aioredis.Redis,
    agent=None,
    tool_name: str | None = None,
) -> None:
    """
    Apply Redis-based rate limiting.

    Skipped when ``app.state.rate_limit_enabled`` is ``False`` (used in tests).
    Limits:
      - 120 requests/60s per IP (global)
      - 5 requests/60s per IP for signup
      - 60 requests/60s per authenticated agent
    """
    if getattr(request.app.state, "rate_limit_enabled", True) is False:
        return

    client_ip = request.client.host if request.client else "unknown"

    # Global per-IP rate limit
    await _check_rate_limit_bucket(redis, f"ratelimit:ip:{client_ip}", 120, 60)

    if tool_name == "signup":
        # Stricter limit for unauthenticated signup
        await _check_rate_limit_bucket(redis, f"ratelimit:ip:{client_ip}:signup", 5, 60)
    elif agent is not None:
        # Per-agent rate limit for authenticated calls
        await _check_rate_limit_bucket(redis, f"ratelimit:agent:{agent.id}", 60, 60)


# ---------------------------------------------------------------------------
# Exception handler registration
# ---------------------------------------------------------------------------


def register_error_handlers(app) -> None:
    """
    Register the ToolError exception handler on the FastAPI application.

    Call this from main.py after including the router::

        from backend.rest.router import router, register_error_handlers
        app.include_router(router)
        register_error_handlers(app)
    """

    @app.exception_handler(ToolError)
    async def tool_error_handler(request: Request, exc: ToolError):
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "error_code": exc.code,
                "message": exc.message,
            },
        )


# ---------------------------------------------------------------------------
# Helper to extract request body safely
# ---------------------------------------------------------------------------


async def _body_or_empty(request: Request) -> dict:
    """Return the parsed JSON body, or an empty dict if the body is empty."""
    body = await request.body()
    if not body or body.strip() == b"":
        return {}
    return await request.json()
