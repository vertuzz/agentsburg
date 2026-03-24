"""
Conflict detection for the spectator experience.

Scans the economy for interesting competitive situations:
- Price wars: 2+ businesses selling the same good in the same zone
- Market cornering: one agent holds >50% of a good's total supply
- Election battles: top two government templates within 2 votes

Results are cached in Redis for 5 minutes.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from backend.models.agent import Agent
from backend.models.business import Business, StorefrontPrice
from backend.models.government import Vote
from backend.models.inventory import InventoryItem

if TYPE_CHECKING:
    import redis.asyncio as aioredis
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.clock import Clock

logger = logging.getLogger(__name__)

CACHE_KEY = "spectator:conflicts"
CACHE_TTL = 300  # 5 minutes


async def detect_conflicts(
    db: AsyncSession,
    redis: aioredis.Redis,
    clock: Clock,
    settings: object,
) -> list[dict]:
    """
    Detect active conflicts in the economy.

    Returns a list of conflict dicts, each with type, agents, detail, severity.
    Cached in Redis for 5 minutes.
    """
    # Check cache first
    try:
        cached = await redis.get(CACHE_KEY)
        if cached:
            return json.loads(cached)
    except Exception:
        logger.debug("Redis cache miss for conflicts")

    conflicts: list[dict] = []

    try:
        conflicts.extend(await _detect_price_wars(db))
    except Exception:
        logger.exception("Error detecting price wars")

    try:
        conflicts.extend(await _detect_market_cornering(db))
    except Exception:
        logger.exception("Error detecting market cornering")

    try:
        conflicts.extend(await _detect_election_battles(db))
    except Exception:
        logger.exception("Error detecting election battles")

    # Cache result
    try:
        await redis.set(CACHE_KEY, json.dumps(conflicts), ex=CACHE_TTL)
    except Exception:
        logger.debug("Failed to cache conflicts")

    return conflicts


async def _detect_price_wars(db: AsyncSession) -> list[dict]:
    """Find goods where 2+ businesses in the same zone have storefront prices."""
    conflicts: list[dict] = []

    # Query: join StorefrontPrice with Business (open only),
    # group by (zone_id, good_slug), find groups with 2+ businesses
    stmt = (
        select(
            Business.zone_id,
            StorefrontPrice.good_slug,
            func.count(func.distinct(StorefrontPrice.business_id)).label("biz_count"),
        )
        .join(Business, Business.id == StorefrontPrice.business_id)
        .where(Business.closed_at.is_(None))
        .group_by(Business.zone_id, StorefrontPrice.good_slug)
        .having(func.count(func.distinct(StorefrontPrice.business_id)) >= 2)
    )

    result = await db.execute(stmt)
    rows = result.all()

    for row in rows:
        zone_id, good_slug, biz_count = row

        # Fetch the individual businesses and their prices
        detail_stmt = (
            select(
                Business.name,
                StorefrontPrice.price,
            )
            .join(Business, Business.id == StorefrontPrice.business_id)
            .where(
                Business.zone_id == zone_id,
                StorefrontPrice.good_slug == good_slug,
                Business.closed_at.is_(None),
            )
        )
        detail_result = await db.execute(detail_stmt)
        detail_rows = detail_result.all()

        business_names = [r.name for r in detail_rows]
        prices = [float(r.price) for r in detail_rows]
        price_strs = [f"{name} @ {price:.0f}" for name, price in zip(business_names, prices, strict=False)]

        conflicts.append(
            {
                "type": "price_war",
                "agents": business_names,
                "detail": f"{biz_count} businesses competing on {good_slug}: {', '.join(price_strs)}",
                "severity": "high" if biz_count >= 3 else "medium",
            }
        )

    return conflicts


async def _detect_market_cornering(db: AsyncSession) -> list[dict]:
    """Find agents holding >50% of a good's total supply (minimum 6 total)."""
    conflicts: list[dict] = []

    # Total supply per good (agent inventories only)
    total_stmt = (
        select(
            InventoryItem.good_slug,
            func.sum(InventoryItem.quantity).label("total_qty"),
        )
        .where(InventoryItem.owner_type == "agent")
        .group_by(InventoryItem.good_slug)
        .having(func.sum(InventoryItem.quantity) > 5)
    )

    total_result = await db.execute(total_stmt)
    good_totals = {row.good_slug: int(row.total_qty) for row in total_result.all()}

    if not good_totals:
        return conflicts

    # Per-agent holdings for goods with enough supply
    for good_slug, total_qty in good_totals.items():
        agent_stmt = (
            select(
                InventoryItem.owner_id,
                InventoryItem.quantity,
            )
            .where(
                InventoryItem.owner_type == "agent",
                InventoryItem.good_slug == good_slug,
            )
            .order_by(InventoryItem.quantity.desc())
            .limit(1)
        )
        agent_result = await db.execute(agent_stmt)
        top_row = agent_result.first()

        if top_row and int(top_row.quantity) > total_qty * 0.5:
            # Look up agent name
            name_result = await db.execute(select(Agent.name).where(Agent.id == top_row.owner_id))
            agent_name = name_result.scalar() or "Unknown"

            pct = round(int(top_row.quantity) / total_qty * 100, 1)
            conflicts.append(
                {
                    "type": "market_cornering",
                    "agents": [agent_name],
                    "detail": f"{agent_name} holds {pct}% of all {good_slug} ({top_row.quantity}/{total_qty})",
                    "severity": "high" if pct >= 80 else "medium",
                }
            )

    return conflicts


async def _detect_election_battles(db: AsyncSession) -> list[dict]:
    """Find close elections where top two templates are within 2 votes."""
    conflicts: list[dict] = []

    stmt = (
        select(
            Vote.template_slug,
            func.count(Vote.id).label("vote_count"),
        )
        .group_by(Vote.template_slug)
        .order_by(func.count(Vote.id).desc())
    )

    result = await db.execute(stmt)
    rows = result.all()

    if len(rows) >= 2:
        first_slug, first_count = rows[0].template_slug, rows[0].vote_count
        second_slug, second_count = rows[1].template_slug, rows[1].vote_count

        if first_count - second_count <= 2:
            conflicts.append(
                {
                    "type": "election_battle",
                    "agents": [first_slug, second_slug],
                    "detail": (
                        f"Close election: {first_slug} ({first_count} votes) vs {second_slug} ({second_count} votes)"
                    ),
                    "severity": "high" if first_count == second_count else "medium",
                }
            )

    return conflicts
