"""
API endpoints: business list and business detail.
"""

from __future__ import annotations

import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.agent import Agent
from backend.models.business import Business, Employment, StorefrontPrice
from backend.models.inventory import InventoryItem
from backend.models.zone import Zone

router = APIRouter(tags=["api"])


@router.get("/businesses")
async def get_businesses_list(
    zone: str | None = Query(None, description="Filter by zone slug"),
    type: str | None = Query(None, description="Filter by business type slug"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Public list of all open businesses with optional zone and type filters.
    """
    # Build base query filters
    filters = [Business.closed_at.is_(None)]

    if zone:
        zone_result = await db.execute(select(Zone).where(Zone.slug == zone))
        zone_obj = zone_result.scalar_one_or_none()
        if zone_obj is None:
            raise HTTPException(status_code=404, detail=f"Zone {zone!r} not found")
        filters.append(Business.zone_id == zone_obj.id)

    if type:
        filters.append(Business.type_slug == type)

    # Count total matching
    count_result = await db.execute(select(func.count(Business.id)).where(and_(*filters)))
    total = count_result.scalar() or 0

    offset = (page - 1) * page_size

    # Fetch page
    biz_result = await db.execute(
        select(Business).where(and_(*filters)).order_by(desc(Business.created_at)).offset(offset).limit(page_size)
    )
    businesses = biz_result.scalars().all()

    # Resolve owner names
    owner_ids = list({b.owner_id for b in businesses})
    owners_map: dict = {}
    if owner_ids:
        owners_result = await db.execute(select(Agent).where(Agent.id.in_(owner_ids)))
        owners_map = {a.id: a for a in owners_result.scalars().all()}

    # Resolve zones
    zone_ids = list({b.zone_id for b in businesses})
    zones_map: dict = {}
    if zone_ids:
        zones_result = await db.execute(select(Zone).where(Zone.id.in_(zone_ids)))
        zones_map = {z.id: z for z in zones_result.scalars().all()}

    # Employee counts
    biz_ids = [b.id for b in businesses]
    emp_counts: dict = {}
    if biz_ids:
        emp_count_result = await db.execute(
            select(
                Employment.business_id,
                func.count(Employment.id).label("cnt"),
            )
            .where(
                and_(
                    Employment.business_id.in_(biz_ids),
                    Employment.terminated_at.is_(None),
                )
            )
            .group_by(Employment.business_id)
        )
        emp_counts = {row.business_id: int(row.cnt) for row in emp_count_result.all()}

    businesses_list = []
    for biz in businesses:
        owner = owners_map.get(biz.owner_id)
        z = zones_map.get(biz.zone_id)

        businesses_list.append(
            {
                "id": str(biz.id),
                "name": biz.name,
                "type_slug": biz.type_slug,
                "owner_name": owner.name if owner else "Unknown",
                "owner_id": str(biz.owner_id),
                "is_npc": biz.is_npc,
                "zone": {"slug": z.slug, "name": z.name} if z else None,
                "employee_count": emp_counts.get(biz.id, 0),
                "is_open": biz.is_open(),
                "created_at": biz.created_at.isoformat(),
            }
        )

    return {
        "businesses": businesses_list,
        "total": total,
    }


@router.get("/businesses/{business_id}")
async def get_business_detail(
    business_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Business detail with inventory, employees, and storefront prices.
    """
    try:
        uid = _uuid.UUID(business_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid business_id format")

    result = await db.execute(select(Business).where(Business.id == uid))
    biz = result.scalar_one_or_none()
    if biz is None:
        raise HTTPException(status_code=404, detail="Business not found")

    # Owner name
    owner_result = await db.execute(select(Agent).where(Agent.id == biz.owner_id))
    owner = owner_result.scalar_one_or_none()

    # Zone
    zone_result = await db.execute(select(Zone).where(Zone.id == biz.zone_id))
    zone = zone_result.scalar_one_or_none()

    # Inventory
    inv_result = await db.execute(
        select(InventoryItem).where(
            and_(
                InventoryItem.owner_type == "business",
                InventoryItem.owner_id == biz.id,
                InventoryItem.quantity > 0,
            )
        )
    )
    inventory = [{"good_slug": item.good_slug, "quantity": item.quantity} for item in inv_result.scalars().all()]

    # Storefront prices
    prices_result = await db.execute(select(StorefrontPrice).where(StorefrontPrice.business_id == biz.id))
    storefront_prices = [{"good_slug": sp.good_slug, "price": float(sp.price)} for sp in prices_result.scalars().all()]

    # Employees
    emp_result = await db.execute(
        select(Employment, Agent)
        .join(Agent, Agent.id == Employment.agent_id)
        .where(
            and_(
                Employment.business_id == biz.id,
                Employment.terminated_at.is_(None),
            )
        )
    )
    employees = []
    for emp, agent in emp_result.all():
        employees.append(
            {
                "agent_id": str(emp.agent_id),
                "agent_name": agent.name,
                "wage_per_work": float(emp.wage_per_work),
                "product_slug": emp.product_slug,
            }
        )

    return {
        "id": str(biz.id),
        "name": biz.name,
        "type_slug": biz.type_slug,
        "owner_name": owner.name if owner else "Unknown",
        "owner_id": str(biz.owner_id),
        "is_npc": biz.is_npc,
        "zone": {"slug": zone.slug, "name": zone.name} if zone else None,
        "storage_capacity": biz.storage_capacity,
        "is_open": biz.is_open(),
        "inventory": inventory,
        "storefront_prices": storefront_prices,
        "employees": employees,
        "created_at": biz.created_at.isoformat(),
    }
