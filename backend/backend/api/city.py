"""
City visualization API endpoint.

Returns a single, cache-friendly response with everything the 3D city
visualization needs: zone GDP, agent activities, sector breakdowns,
and figurine scaling data.

Read-only aggregation — no writes, no economy state changes.
"""

from __future__ import annotations

import json
import logging
import math
from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.agent import Agent
from backend.models.business import Business, Employment
from backend.models.marketplace import MarketOrder, Trade
from backend.models.transaction import Transaction
from backend.models.zone import Zone

logger = logging.getLogger(__name__)

router = APIRouter(tags=["api"])

# ── Sector classification ──

SECTOR_MAP: dict[str, str] = {
    # Extraction (Tier 1 production)
    "farm": "extraction",
    "mine": "extraction",
    "lumber_mill": "extraction",
    "fishing_operation": "extraction",
    # Manufacturing (Tier 2 processing)
    "mill": "manufacturing",
    "smithy": "manufacturing",
    "kiln": "manufacturing",
    "textile_shop": "manufacturing",
    "tannery": "manufacturing",
    "glassworks": "manufacturing",
    "apothecary": "manufacturing",
    "workshop": "manufacturing",
    # Retail (Tier 3 finished goods)
    "bakery": "retail",
    "brewery": "retail",
    "jeweler": "retail",
    "general_store": "retail",
}

SECTOR_ORDER = ["extraction", "manufacturing", "retail", "services"]

# Numeric wealth tier ranking for proper sorting (higher = wealthier)
WEALTH_TIER_RANK: dict[str, int] = {
    "rich": 5,
    "upper_middle": 4,
    "middle": 3,
    "lower_middle": 2,
    "poor": 1,
}


def classify_sector(type_slug: str) -> str:
    """Map a business type_slug to one of the four economy sectors."""
    return SECTOR_MAP.get(type_slug, "services")


def compute_scale(population: int) -> dict:
    """Compute figurine scaling ratio for the given population."""
    max_figurines = 100
    if population <= max_figurines:
        return {"population": population, "figurine_ratio": 1, "figurine_count": population}
    ratio = math.ceil(population / max_figurines)
    return {"population": population, "figurine_ratio": ratio, "figurine_count": max_figurines}


async def _batch_load_cooldowns(redis, agent_ids: list[str]) -> tuple[set[str], set[str]]:
    """
    Batch-load work and gather cooldowns for all agents in one SCAN pass.

    Returns (working_set, gathering_set) of agent ID strings.

    Cooldown key formats:
      - Work:    cooldown:work:{agent_id}
      - Gather:  cooldown:gather:{agent_id}:{resource_slug}
      - Gather global: cooldown:gather_global:{agent_id}
    """
    working_ids: set[str] = set()
    gathering_ids: set[str] = set()
    agent_id_set = set(agent_ids)

    # Use SCAN instead of KEYS to avoid blocking Redis
    cursor = 0
    while True:
        cursor, keys = await redis.scan(cursor, match="cooldown:*", count=200)
        for key in keys:
            parts = key.split(":")
            if len(parts) < 3:
                continue

            cooldown_type = parts[1]  # "work", "gather", or "gather_global"

            if cooldown_type == "work" and len(parts) == 3:
                # cooldown:work:{agent_id}
                aid = parts[2]
                if aid in agent_id_set:
                    working_ids.add(aid)

            elif cooldown_type == "gather" and len(parts) >= 3:
                # cooldown:gather:{agent_id}:{resource_slug}
                aid = parts[2]
                if aid in agent_id_set:
                    gathering_ids.add(aid)

            elif cooldown_type == "gather_global" and len(parts) == 3:
                # cooldown:gather_global:{agent_id}
                aid = parts[2]
                if aid in agent_id_set:
                    gathering_ids.add(aid)

        if cursor == 0:
            break

    return working_ids, gathering_ids


def classify_agent_activity(
    agent: Agent,
    now: datetime,
    working_ids: set[str],
    gathering_ids: set[str],
    employed_map: dict,
    open_orders_set: set,
    pending_trades_set: set,
    business_owner_map: dict,
) -> tuple[str, str]:
    """
    Determine an agent's current activity from existing state.

    Returns (activity, activity_detail) tuple.
    Priority ordering matches the design plan.
    """
    agent_id = str(agent.id)

    # 1. Inactive
    if not agent.is_active:
        return "inactive", "deactivated"

    # 2. Jailed
    if agent.jail_until and agent.jail_until > now:
        return "jailed", "serving time"

    # 3. Homeless
    if agent.housing_zone_id is None:
        return "homeless", "wandering"

    # 4. Working (has active work cooldown)
    if agent_id in working_ids:
        return "working", "producing goods"

    # 5. Gathering (has active gather cooldown)
    if agent_id in gathering_ids:
        return "gathering", "gathering resources"

    # 6. Trading (has open marketplace orders)
    if agent_id in open_orders_set:
        return "trading", "trading on marketplace"

    # 7. Negotiating (has pending trade proposals)
    if agent_id in pending_trades_set:
        return "negotiating", "negotiating a deal"

    # 8. Employed
    if agent_id in employed_map:
        biz_name = employed_map[agent_id]
        return "employed", f"employed at {biz_name}"

    # 9. Managing (owns open businesses)
    if agent_id in business_owner_map:
        biz_name = business_owner_map[agent_id]
        return "managing", f"managing {biz_name}"

    # 10. Default: idle
    return "idle", "resting"


def classify_wealth_tier(balance: float) -> str:
    """Classify an agent into a wealth tier based on balance."""
    if balance >= 5000:
        return "rich"
    if balance >= 1000:
        return "upper_middle"
    if balance >= 200:
        return "middle"
    if balance >= 50:
        return "lower_middle"
    return "poor"


@router.get("/city")
async def get_city(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    City visualization data: zones with GDP, agents with activities,
    economy sectors, and figurine scaling.

    Redis-cached with 10s TTL to avoid DB pressure from frequent polls.
    """
    redis = request.app.state.redis
    clock = request.app.state.clock
    now = clock.now()

    # ── Check cache ──
    cached = await redis.get("city:visualization")
    if cached is not None:
        try:
            return json.loads(cached)
        except Exception:
            pass

    # ── Load all zones ──
    zones_result = await db.execute(select(Zone).order_by(Zone.name))
    zones = zones_result.scalars().all()
    zone_slug_by_id: dict[str, str] = {str(z.id): z.slug for z in zones}

    # ── Load all active agents ──
    agents_result = await db.execute(select(Agent))
    all_agents = agents_result.scalars().all()

    # ── Load active businesses ──
    biz_result = await db.execute(select(Business).where(Business.closed_at.is_(None)))
    businesses = biz_result.scalars().all()

    # Build business lookup maps
    biz_by_zone: dict[str, list[Business]] = defaultdict(list)
    business_owner_map: dict[str, str] = {}  # agent_id -> first business name
    biz_by_id: dict[str, Business] = {}
    for biz in businesses:
        zone_slug = zone_slug_by_id.get(str(biz.zone_id), "outskirts")
        biz_by_zone[zone_slug].append(biz)
        biz_by_id[str(biz.id)] = biz
        owner_id = str(biz.owner_id)
        if owner_id not in business_owner_map:
            business_owner_map[owner_id] = biz.name

    # ── Load active employments ──
    emp_result = await db.execute(select(Employment).where(Employment.terminated_at.is_(None)))
    employments = emp_result.scalars().all()
    employed_map: dict[str, str] = {}  # agent_id -> business name
    for emp in employments:
        agent_id = str(emp.agent_id)
        biz = biz_by_id.get(str(emp.business_id))
        if biz and agent_id not in employed_map:
            employed_map[agent_id] = biz.name

    # ── Load open marketplace orders (agent IDs) ──
    open_orders_result = await db.execute(
        select(func.distinct(MarketOrder.agent_id)).where(MarketOrder.status.in_(["open", "partially_filled"]))
    )
    open_orders_set = {str(row[0]) for row in open_orders_result.all()}

    # ── Load pending trades (agent IDs) ──
    pending_trades_set: set[str] = set()
    try:
        pending_result = await db.execute(
            select(Trade.proposer_id, Trade.responder_id).where(Trade.status == "pending")
        )
        for row in pending_result.all():
            pending_trades_set.add(str(row[0]))
            if row[1]:
                pending_trades_set.add(str(row[1]))
    except Exception:
        logger.debug("Could not query pending trades for city view", exc_info=True)

    # ── Batch-load cooldowns (single SCAN, not per-agent KEYS) ──
    active_agent_ids = [str(a.id) for a in all_agents if a.is_active]
    working_ids, gathering_ids = await _batch_load_cooldowns(redis, active_agent_ids)

    # ── GDP per zone (last 6h from transactions) ──
    six_hours_ago = now - timedelta(hours=6)
    gdp_by_zone: dict[str, float] = defaultdict(float)

    # Storefront + marketplace transactions with zone info from metadata
    tx_result = await db.execute(
        select(Transaction.amount, Transaction.metadata_json).where(
            and_(
                Transaction.type.in_(["storefront", "marketplace", "wage"]),
                Transaction.created_at >= six_hours_ago,
            )
        )
    )
    for row in tx_result.all():
        amount = float(row.amount)
        meta = row.metadata_json or {}
        zone_slug = meta.get("zone_slug", "outskirts")
        gdp_by_zone[zone_slug] += amount

    total_gdp = sum(gdp_by_zone.values())

    # ── Classify agents by zone and activity ──
    zone_agents: dict[str, list[dict]] = defaultdict(list)
    total_population = 0

    for agent in all_agents:
        if not agent.is_active:
            continue

        total_population += 1

        # Determine zone
        if agent.housing_zone_id:
            zone_slug = zone_slug_by_id.get(str(agent.housing_zone_id), "outskirts")
        else:
            zone_slug = "outskirts"

        activity, activity_detail = classify_agent_activity(
            agent,
            now,
            working_ids,
            gathering_ids,
            employed_map,
            open_orders_set,
            pending_trades_set,
            business_owner_map,
        )

        zone_agents[zone_slug].append(
            {
                "id": str(agent.id),
                "name": agent.name,
                "model": agent.model,
                "activity": activity,
                "activity_detail": activity_detail,
                "wealth_tier": classify_wealth_tier(float(agent.balance)),
                "is_jailed": agent.jail_until is not None and agent.jail_until > now,
                "avatar_url": None,
            }
        )

    # ── Build zone response ──
    scale = compute_scale(total_population)

    # Sector aggregation
    sector_data: dict[str, dict] = {s: {"gdp": 0.0, "share": 0.0, "businesses": 0, "workers": 0} for s in SECTOR_ORDER}

    # Count workers per business
    workers_per_biz: dict[str, int] = defaultdict(int)
    for emp in employments:
        workers_per_biz[str(emp.business_id)] += 1

    for biz in businesses:
        sector = classify_sector(biz.type_slug)
        sector_data[sector]["businesses"] += 1
        sector_data[sector]["workers"] += workers_per_biz.get(str(biz.id), 0)

    zone_list = []
    for zone in zones:
        z_slug = zone.slug
        z_gdp = gdp_by_zone.get(z_slug, 0.0)
        z_gdp_share = (z_gdp / total_gdp) if total_gdp > 0 else 0.0
        z_agents = zone_agents.get(z_slug, [])
        z_businesses = biz_by_zone.get(z_slug, [])

        # Business breakdown by sector
        by_sector: dict[str, int] = {s: 0 for s in SECTOR_ORDER}
        npc_count = 0
        agent_biz_count = 0
        for biz in z_businesses:
            sector = classify_sector(biz.type_slug)
            by_sector[sector] += 1
            if biz.is_npc:
                npc_count += 1
            else:
                agent_biz_count += 1

        # Accumulate sector GDP proportionally
        for sector_name in SECTOR_ORDER:
            if by_sector[sector_name] > 0 and len(z_businesses) > 0:
                sector_data[sector_name]["gdp"] += z_gdp * (by_sector[sector_name] / len(z_businesses))

        # Build agent response: full list for small populations, aggregated for large
        if total_population <= 200:
            agents_response = z_agents
            agent_counts = None
        else:
            # Sort by wealth tier (numeric rank) for top agents
            agents_response = sorted(
                z_agents,
                key=lambda a: WEALTH_TIER_RANK.get(a["wealth_tier"], 0),
                reverse=True,
            )[:5]
            # Activity counts for the full population
            agent_counts: dict[str, int] = {}
            for a in z_agents:
                agent_counts[a["activity"]] = agent_counts.get(a["activity"], 0) + 1

        zone_entry: dict = {
            "slug": z_slug,
            "name": zone.name,
            "rent_cost": float(zone.rent_cost),
            "foot_traffic": zone.foot_traffic,
            "gdp_6h": round(z_gdp, 2),
            "gdp_share": round(z_gdp_share, 4),
            "population": len(z_agents),
            "businesses": {
                "total": len(z_businesses),
                "npc": npc_count,
                "agent": agent_biz_count,
                "by_sector": by_sector,
            },
            "agents": agents_response,
        }
        if agent_counts is not None:
            zone_entry["agent_counts"] = agent_counts
        zone_list.append(zone_entry)

    # Finalize sector shares
    for sector_name in SECTOR_ORDER:
        if total_gdp > 0:
            sector_data[sector_name]["share"] = round(sector_data[sector_name]["gdp"] / total_gdp, 4)
        sector_data[sector_name]["gdp"] = round(sector_data[sector_name]["gdp"], 2)

    result = {
        "zones": zone_list,
        "economy": {
            "total_gdp_6h": round(total_gdp, 2),
            "population": total_population,
            "sectors": sector_data,
        },
        "scale": scale,
        "cached_at": now.isoformat(),
    }

    # ── Cache result ──
    await redis.setex("city:visualization", 10, json.dumps(result))

    return result
