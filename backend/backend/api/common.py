"""
Shared helpers and imports for the API sub-modules.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException
from sqlalchemy import select

from backend.models.agent import Agent

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def get_agent_from_view_token(token: str, db: AsyncSession) -> Agent:
    """
    Look up an agent by their view_token.

    Raises HTTP 401 if the token is missing or invalid.
    """
    if not token:
        raise HTTPException(status_code=401, detail="view_token required")
    result = await db.execute(select(Agent).where(Agent.view_token == token))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=401, detail="Invalid view_token")
    return agent
