"""
MCP authentication for Agent Economy.

Agents authenticate by passing their action_token as a Bearer token in the
Authorization HTTP header on every MCP request:

    Authorization: Bearer <action_token>

The signup tool is the single unauthenticated entry point — no token needed
to create a new agent.

All other tool calls must include a valid Bearer token. If the token is
missing or invalid, the request is rejected with an UNAUTHORIZED error.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.service import get_agent_by_action_token

if TYPE_CHECKING:
    from backend.models.agent import Agent


async def get_agent_from_request(
    request: Request,
    db: AsyncSession,
) -> "Agent | None":
    """
    Extract and validate the Bearer token from the HTTP request.

    Reads the Authorization header, strips the "Bearer " prefix, and
    looks up the corresponding agent in the database.

    Args:
        request: The incoming FastAPI request.
        db:      Active async database session.

    Returns:
        The Agent if the token is valid, None otherwise (missing header,
        malformed header, or unknown token).
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None

    token = auth_header[len("Bearer "):].strip()
    if not token:
        return None

    return await get_agent_by_action_token(db, token)
