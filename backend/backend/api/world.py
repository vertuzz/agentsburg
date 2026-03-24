"""
API endpoints: zones, government, and goods.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.agent import Agent
from backend.models.business import Business, StorefrontPrice
from backend.models.good import Good
from backend.models.government import GovernmentState, Vote
from backend.models.marketplace import MarketOrder, MarketTrade
from backend.models.zone import Zone

router = APIRouter(tags=["api"])


@router.get("/zones")
async def get_zones(
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    All zones with population, business counts, and top goods sold.
    """
    zones_result = await db.execute(select(Zone).order_by(Zone.name))
    zones = zones_result.scalars().all()

    zone_list = []
    for zone in zones:
        # Business count (NPC vs agent-owned)
        npc_count_result = await db.execute(
            select(func.count(Business.id)).where(
                and_(
                    Business.zone_id == zone.id,
                    Business.is_npc.is_(True),
                    Business.closed_at.is_(None),
                )
            )
        )
        npc_count = npc_count_result.scalar() or 0

        agent_count_result = await db.execute(
            select(func.count(Business.id)).where(
                and_(
                    Business.zone_id == zone.id,
                    Business.is_npc.is_(False),
                    Business.closed_at.is_(None),
                )
            )
        )
        agent_count = agent_count_result.scalar() or 0

        # Population: agents with housing in this zone
        pop_result = await db.execute(select(func.count(Agent.id)).where(Agent.housing_zone_id == zone.id))
        population = pop_result.scalar() or 0

        # Top goods sold (by storefront transaction volume, last 7d, filtered by zone)
        one_week_ago = datetime.now(UTC) - timedelta(days=7)
        from sqlalchemy import text as _text

        top_goods_result = await db.execute(
            _text(
                "SELECT metadata_json->>'good_slug' AS good_slug, SUM(amount) AS total "
                "FROM transactions "
                "WHERE type = 'storefront' "
                "  AND created_at >= :one_week_ago "
                "  AND metadata_json->>'zone_slug' = :zone_slug "
                "GROUP BY metadata_json->>'good_slug' "
                "ORDER BY total DESC "
                "LIMIT 5"
            ),
            {"one_week_ago": one_week_ago, "zone_slug": zone.slug},
        )
        top_goods_rows = top_goods_result.all()
        top_goods = [
            {"good_slug": row.good_slug, "revenue": float(row.total)} for row in top_goods_rows if row.good_slug
        ]

        zone_list.append(
            {
                "id": str(zone.id),
                "slug": zone.slug,
                "name": zone.name,
                "rent_cost": float(zone.rent_cost),
                "foot_traffic": zone.foot_traffic,
                "demand_multiplier": zone.demand_multiplier,
                "allowed_business_types": zone.allowed_business_types,
                "businesses": {
                    "npc": npc_count,
                    "agent": agent_count,
                    "total": npc_count + agent_count,
                },
                "population": population,
                "top_goods": top_goods,
            }
        )

    return {"zones": zone_list}


@router.get("/government")
async def get_government(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Current government info: template, vote counts, election timing.
    """
    settings = request.app.state.settings
    now = datetime.now(UTC)

    # Current government state
    gov_result = await db.execute(select(GovernmentState).where(GovernmentState.id == 1))
    gov_state = gov_result.scalar_one_or_none()

    current_slug = gov_state.current_template_slug if gov_state else "free_market"
    last_election_at = gov_state.last_election_at if gov_state else None

    # Time until next election (weekly)
    if last_election_at:
        next_election_at = last_election_at + timedelta(weeks=1)
        seconds_until_election = max(0, (next_election_at - now).total_seconds())
    else:
        seconds_until_election = 0
        next_election_at = None

    # Current template params
    templates = settings.government.get("templates", [])
    current_params: dict[str, Any] = {}
    for tmpl in templates:
        if tmpl.get("slug") == current_slug:
            current_params = dict(tmpl)
            break

    # Vote counts per template
    votes_result = await db.execute(
        select(
            Vote.template_slug,
            func.count(Vote.id).label("count"),
        ).group_by(Vote.template_slug)
    )
    vote_rows = votes_result.all()
    vote_counts = {row.template_slug: int(row.count) for row in vote_rows}

    # All available templates (for display)
    all_templates = [
        {
            "slug": t.get("slug", ""),
            "name": t.get("name", t.get("slug", "")),
            "description": t.get("description", ""),
            "tax_rate": t.get("tax_rate", 0),
            "enforcement_probability": t.get("enforcement_probability", 0),
            "interest_rate_modifier": t.get("interest_rate_modifier", 1.0),
            "vote_count": vote_counts.get(t.get("slug", ""), 0),
        }
        for t in templates
    ]

    # Recent election history (last 5 election transitions)
    # For now we just have one gov state -- extend when we log elections
    election_history: list[dict] = []
    if gov_state and gov_state.last_election_at:
        election_history.append(
            {
                "template": current_slug,
                "template_name": current_params.get("name", current_slug),
                "tallied_at": gov_state.last_election_at.isoformat(),
            }
        )

    return {
        "current_template": current_params,
        "templates": all_templates,
        "vote_counts": vote_counts,
        "total_votes": sum(vote_counts.values()),
        "seconds_until_election": seconds_until_election,
        "next_election_at": next_election_at.isoformat() if next_election_at else None,
        "last_election_at": last_election_at.isoformat() if last_election_at else None,
        "election_history": election_history,
    }


@router.get("/goods")
async def get_goods(
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    All goods with current market prices (best available sell price).
    """
    goods_result = await db.execute(select(Good).order_by(Good.tier, Good.slug))
    goods = goods_result.scalars().all()

    # For each good, get best sell price from open orders
    good_slugs = [g.slug for g in goods]
    prices_result = await db.execute(
        select(
            MarketOrder.good_slug,
            func.min(MarketOrder.price).label("best_sell"),
        )
        .where(
            and_(
                MarketOrder.good_slug.in_(good_slugs),
                MarketOrder.side == "sell",
                MarketOrder.status.in_(["open", "partially_filled"]),
            )
        )
        .group_by(MarketOrder.good_slug)
    )
    best_sell_prices = {row.good_slug: float(row.best_sell) for row in prices_result.all()}

    # Also get storefront prices for context
    storefront_result = await db.execute(
        select(
            StorefrontPrice.good_slug,
            func.min(StorefrontPrice.price).label("best_storefront"),
        ).group_by(StorefrontPrice.good_slug)
    )
    best_storefront = {row.good_slug: float(row.best_storefront) for row in storefront_result.all()}

    # Last trade price for each good
    last_trade_result = await db.execute(
        select(
            MarketTrade.good_slug,
            MarketTrade.price,
        )
        .distinct(MarketTrade.good_slug)
        .order_by(MarketTrade.good_slug, desc(MarketTrade.executed_at))
    )
    last_trade_prices = {row.good_slug: float(row.price) for row in last_trade_result.all()}

    goods_list = []
    for g in goods:
        goods_list.append(
            {
                **g.to_dict(),
                "best_sell_price": best_sell_prices.get(g.slug),
                "best_storefront_price": best_storefront.get(g.slug),
                "last_trade_price": last_trade_prices.get(g.slug),
            }
        )

    return {"goods": goods_list}
