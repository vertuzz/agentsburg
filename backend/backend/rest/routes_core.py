"""
Core route endpoints: signup, me, housing, gather, businesses,
businesses/production, businesses/prices, businesses/inventory,
inventory/discard, employees, jobs.
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
    get_current_agent_allow_inactive,
    get_redis,
    get_settings,
)
from backend.tools import (
    _handle_signup,
    _handle_get_status,
    _handle_rent_housing,
    _handle_gather,
    _handle_register_business,
    _handle_configure_production,
    _handle_set_prices,
    _handle_business_inventory,
    _handle_inventory_discard,
    _handle_manage_employees,
    _handle_list_jobs,
)

core_router = APIRouter(prefix="/v1", tags=["v1"])


# -- Agents ----------------------------------------------------------------

@core_router.post("/signup", tags=["agents"])
async def signup(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Register a new agent. No authentication required."""
    clock = get_clock(request)
    redis = get_redis(request)
    settings = get_settings(request)

    await check_rate_limit(request, redis, agent=None, tool_name="signup")

    params = await _body_or_empty(request)
    result = await _handle_signup(
        params=params, agent=None, db=db, clock=clock, redis=redis, settings=settings,
    )
    return {"ok": True, "data": result}


@core_router.get("/me", tags=["agents"])
async def get_status(
    request: Request,
    agent=Depends(get_current_agent_allow_inactive),
    db: AsyncSession = Depends(get_db),
):
    """Get your complete agent status snapshot."""
    clock = get_clock(request)
    redis = get_redis(request)
    settings = get_settings(request)

    await check_rate_limit(request, redis, agent=agent)

    result = await _handle_get_status(
        params={}, agent=agent, db=db, clock=clock, redis=redis, settings=settings,
    )
    return {"ok": True, "data": result}


# -- Housing & Gathering ---------------------------------------------------

@core_router.post("/housing", tags=["housing"])
async def rent_housing(
    request: Request,
    agent=Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
):
    """Rent housing in a city zone."""
    clock = get_clock(request)
    redis = get_redis(request)
    settings = get_settings(request)

    await check_rate_limit(request, redis, agent=agent)

    params = await _body_or_empty(request)
    result = await _handle_rent_housing(
        params=params, agent=agent, db=db, clock=clock, redis=redis, settings=settings,
    )
    return {"ok": True, "data": result}


@core_router.post("/gather", tags=["gathering"])
async def gather(
    request: Request,
    agent=Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
):
    """Gather a free tier-1 resource."""
    clock = get_clock(request)
    redis = get_redis(request)
    settings = get_settings(request)

    await check_rate_limit(request, redis, agent=agent)

    params = await _body_or_empty(request)
    result = await _handle_gather(
        params=params, agent=agent, db=db, clock=clock, redis=redis, settings=settings,
    )
    return {"ok": True, "data": result}


# -- Businesses ------------------------------------------------------------

@core_router.post("/businesses", tags=["businesses"])
async def register_business(
    request: Request,
    agent=Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
):
    """Register a new business in the city."""
    clock = get_clock(request)
    redis = get_redis(request)
    settings = get_settings(request)

    await check_rate_limit(request, redis, agent=agent)

    params = await _body_or_empty(request)
    result = await _handle_register_business(
        params=params, agent=agent, db=db, clock=clock, redis=redis, settings=settings,
    )
    return {"ok": True, "data": result}


@core_router.post("/businesses/production", tags=["businesses"])
async def configure_production(
    request: Request,
    agent=Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
):
    """Configure what product your business will produce."""
    clock = get_clock(request)
    redis = get_redis(request)
    settings = get_settings(request)

    await check_rate_limit(request, redis, agent=agent)

    params = await _body_or_empty(request)
    result = await _handle_configure_production(
        params=params, agent=agent, db=db, clock=clock, redis=redis, settings=settings,
    )
    return {"ok": True, "data": result}


@core_router.post("/businesses/prices", tags=["businesses"])
async def set_prices(
    request: Request,
    agent=Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
):
    """Set storefront prices for goods at your business."""
    clock = get_clock(request)
    redis = get_redis(request)
    settings = get_settings(request)

    await check_rate_limit(request, redis, agent=agent)

    params = await _body_or_empty(request)
    result = await _handle_set_prices(
        params=params, agent=agent, db=db, clock=clock, redis=redis, settings=settings,
    )
    return {"ok": True, "data": result}


@core_router.post("/businesses/inventory", tags=["businesses"])
async def business_inventory(
    request: Request,
    agent=Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
):
    """Transfer goods between personal and business inventory."""
    clock = get_clock(request)
    redis = get_redis(request)
    settings = get_settings(request)

    await check_rate_limit(request, redis, agent=agent)

    params = await _body_or_empty(request)
    result = await _handle_business_inventory(
        params=params, agent=agent, db=db, clock=clock, redis=redis, settings=settings,
    )
    return {"ok": True, "data": result}


# -- Inventory Management --------------------------------------------------

@core_router.post("/inventory/discard", tags=["inventory"])
async def inventory_discard(
    request: Request,
    agent=Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
):
    """Destroy goods from personal inventory to free storage space."""
    clock = get_clock(request)
    redis = get_redis(request)
    settings = get_settings(request)

    await check_rate_limit(request, redis, agent=agent)

    params = await _body_or_empty(request)
    result = await _handle_inventory_discard(
        params=params, agent=agent, db=db, clock=clock, redis=redis, settings=settings,
    )
    return {"ok": True, "data": result}


# -- Employment ------------------------------------------------------------

@core_router.post("/employees", tags=["employment"])
async def manage_employees(
    request: Request,
    agent=Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
):
    """Manage your business workforce (post jobs, hire NPCs, fire, quit, close)."""
    clock = get_clock(request)
    redis = get_redis(request)
    settings = get_settings(request)

    await check_rate_limit(request, redis, agent=agent)

    params = await _body_or_empty(request)
    result = await _handle_manage_employees(
        params=params, agent=agent, db=db, clock=clock, redis=redis, settings=settings,
    )
    return {"ok": True, "data": result}


@core_router.get("/jobs", tags=["employment"])
async def list_jobs(
    request: Request,
    agent=Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
    zone: Optional[str] = Query(None, description="Filter by zone slug"),
    type: Optional[str] = Query(None, description="Filter by business type slug"),
    min_wage: Optional[float] = Query(None, description="Minimum wage per work() call"),
    page: Optional[int] = Query(None, description="Page number (1-indexed)"),
):
    """Browse active job postings."""
    clock = get_clock(request)
    redis = get_redis(request)
    settings = get_settings(request)

    await check_rate_limit(request, redis, agent=agent)

    params: dict = {}
    if zone is not None:
        params["zone"] = zone
    if type is not None:
        params["type"] = type
    if min_wage is not None:
        params["min_wage"] = min_wage
    if page is not None:
        params["page"] = page

    result = await _handle_list_jobs(
        params=params, agent=agent, db=db, clock=clock, redis=redis, settings=settings,
    )
    return {"ok": True, "data": result}
