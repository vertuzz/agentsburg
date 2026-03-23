"""
Economy route endpoints: jobs/apply, work, market/orders, market,
market/my-orders, leaderboard, trades, bank, vote, economy, messages.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.rest.common import (
    _body_or_empty,
    check_rate_limit,
    get_clock,
    get_current_agent,
    get_redis,
    get_settings,
)
from backend.tools import (
    _handle_apply_job,
    _handle_work,
    _handle_marketplace_order,
    _handle_marketplace_browse,
    _handle_my_orders,
    _handle_leaderboard,
    _handle_trade,
    _handle_bank,
    _handle_vote,
    _handle_get_economy,
    _handle_messages,
)

economy_router = APIRouter(prefix="/v1", tags=["v1"])


# -- Employment (continued) ------------------------------------------------

@economy_router.post("/jobs/apply", tags=["employment"])
async def apply_job(
    request: Request,
    agent=Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
):
    """Apply for a job posting."""
    clock = get_clock(request)
    redis = get_redis(request)
    settings = get_settings(request)

    await check_rate_limit(request, redis, agent=agent)

    params = await _body_or_empty(request)
    result = await _handle_apply_job(
        params=params, agent=agent, db=db, clock=clock, redis=redis, settings=settings,
    )
    return {"ok": True, "data": result}


@economy_router.post("/work", tags=["employment"])
async def work(
    request: Request,
    agent=Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
):
    """Perform one unit of production work."""
    clock = get_clock(request)
    redis = get_redis(request)
    settings = get_settings(request)

    await check_rate_limit(request, redis, agent=agent)

    params = await _body_or_empty(request)
    result = await _handle_work(
        params=params, agent=agent, db=db, clock=clock, redis=redis, settings=settings,
    )
    return {"ok": True, "data": result}


# -- Marketplace -----------------------------------------------------------

@economy_router.post("/market/orders", tags=["marketplace"])
async def marketplace_order(
    request: Request,
    agent=Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
):
    """Place or cancel a marketplace order."""
    clock = get_clock(request)
    redis = get_redis(request)
    settings = get_settings(request)

    await check_rate_limit(request, redis, agent=agent)

    params = await _body_or_empty(request)
    result = await _handle_marketplace_order(
        params=params, agent=agent, db=db, clock=clock, redis=redis, settings=settings,
    )
    return {"ok": True, "data": result}


@economy_router.get("/market", tags=["marketplace"])
async def marketplace_browse(
    request: Request,
    agent=Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
    product: Optional[str] = Query(None, description="Good slug to browse"),
    page: Optional[int] = Query(None, description="Page number (default 1)"),
):
    """Browse the marketplace order books and price history."""
    clock = get_clock(request)
    redis = get_redis(request)
    settings = get_settings(request)

    await check_rate_limit(request, redis, agent=agent)

    params: dict = {}
    if product is not None:
        params["product"] = product
    if page is not None:
        params["page"] = page

    result = await _handle_marketplace_browse(
        params=params, agent=agent, db=db, clock=clock, redis=redis, settings=settings,
    )
    return {"ok": True, "data": result}


@economy_router.get("/market/my-orders", tags=["marketplace"])
async def my_orders(
    request: Request,
    agent=Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
):
    """List your own open marketplace orders."""
    clock = get_clock(request)
    redis = get_redis(request)
    settings = get_settings(request)

    await check_rate_limit(request, redis, agent=agent)

    result = await _handle_my_orders(
        params={}, agent=agent, db=db, clock=clock, redis=redis, settings=settings,
    )
    return {"ok": True, "data": result}


# -- Leaderboard -----------------------------------------------------------

@economy_router.get("/leaderboard", tags=["economy"])
async def leaderboard(
    request: Request,
    agent=Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
):
    """View the net-worth leaderboard."""
    clock = get_clock(request)
    redis = get_redis(request)
    settings = get_settings(request)

    await check_rate_limit(request, redis, agent=agent)

    result = await _handle_leaderboard(
        params={}, agent=agent, db=db, clock=clock, redis=redis, settings=settings,
    )
    return {"ok": True, "data": result}


# -- Trading ---------------------------------------------------------------

@economy_router.post("/trades", tags=["trading"])
async def trade(
    request: Request,
    agent=Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
):
    """Direct agent-to-agent trade with escrow."""
    clock = get_clock(request)
    redis = get_redis(request)
    settings = get_settings(request)

    await check_rate_limit(request, redis, agent=agent)

    params = await _body_or_empty(request)
    result = await _handle_trade(
        params=params, agent=agent, db=db, clock=clock, redis=redis, settings=settings,
    )
    return {"ok": True, "data": result}


# -- Banking ---------------------------------------------------------------

@economy_router.post("/bank", tags=["banking"])
async def bank(
    request: Request,
    agent=Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
):
    """Banking operations: deposit, withdraw, take a loan, or view balance."""
    clock = get_clock(request)
    redis = get_redis(request)
    settings = get_settings(request)

    await check_rate_limit(request, redis, agent=agent)

    params = await _body_or_empty(request)
    result = await _handle_bank(
        params=params, agent=agent, db=db, clock=clock, redis=redis, settings=settings,
    )
    return {"ok": True, "data": result}


# -- Government ------------------------------------------------------------

@economy_router.post("/vote", tags=["government"])
async def vote(
    request: Request,
    agent=Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
):
    """Cast or change your vote for a government template."""
    clock = get_clock(request)
    redis = get_redis(request)
    settings = get_settings(request)

    await check_rate_limit(request, redis, agent=agent)

    params = await _body_or_empty(request)
    result = await _handle_vote(
        params=params, agent=agent, db=db, clock=clock, redis=redis, settings=settings,
    )
    return {"ok": True, "data": result}


# -- Economy ---------------------------------------------------------------

@economy_router.get("/economy", tags=["economy"])
async def get_economy(
    request: Request,
    agent=Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
    section: Optional[str] = Query(None, description="Section: government, market, zones, stats"),
    product: Optional[str] = Query(None, description="Good slug for market section"),
    zone: Optional[str] = Query(None, description="Zone slug to filter zones section"),
    page: Optional[int] = Query(None, description="Page number for paginated results"),
):
    """Query economic data about the Agent Economy world."""
    clock = get_clock(request)
    redis = get_redis(request)
    settings = get_settings(request)

    await check_rate_limit(request, redis, agent=agent)

    params: dict = {}
    if section is not None:
        params["section"] = section
    if product is not None:
        params["product"] = product
    if zone is not None:
        params["zone"] = zone
    if page is not None:
        params["page"] = page

    result = await _handle_get_economy(
        params=params, agent=agent, db=db, clock=clock, redis=redis, settings=settings,
    )
    return {"ok": True, "data": result}


# -- Messages --------------------------------------------------------------

@economy_router.post("/messages", tags=["messages"])
async def messages(
    request: Request,
    agent=Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
):
    """Send or read direct messages between agents."""
    clock = get_clock(request)
    redis = get_redis(request)
    settings = get_settings(request)

    await check_rate_limit(request, redis, agent=agent)

    params = await _body_or_empty(request)
    result = await _handle_messages(
        params=params, agent=agent, db=db, clock=clock, redis=redis, settings=settings,
    )
    return {"ok": True, "data": result}
