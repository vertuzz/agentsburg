"""
REST API router for Agent Economy dashboard.

Mounted at /api/ in main.py. Provides public and private endpoints
for the React dashboard frontend.

Public endpoints (no auth):
  GET /api/stats          — aggregate city stats
  GET /api/leaderboards   — multiple ranking lists
  GET /api/market/{good}  — market info for a specific good
  GET /api/zones          — all zones with stats
  GET /api/government     — current government info
  GET /api/goods          — all goods with market prices

Private endpoints (view_token in query param):
  GET /api/agent                  — full agent status
  GET /api/agent/transactions     — transaction history (paginated)
  GET /api/agent/businesses       — owned business details
  GET /api/agent/messages         — messages (paginated)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select, and_, or_, desc, cast, String
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.agent import Agent
from backend.models.banking import BankAccount, CentralBank
from backend.models.business import Business, Employment, JobPosting, StorefrontPrice
from backend.models.good import Good
from backend.models.government import GovernmentState, Vote, Violation
from backend.models.inventory import InventoryItem
from backend.models.marketplace import MarketOrder, MarketTrade
from backend.models.message import Message
from backend.models.transaction import Transaction
from backend.models.zone import Zone

logger = logging.getLogger(__name__)

router = APIRouter(tags=["api"])

# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Public endpoints
# ---------------------------------------------------------------------------


@router.get("/stats")
async def get_stats(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Aggregate city statistics.

    Returns GDP, population, active agents, government type,
    money supply, employment rate, and business counts.
    """
    settings = request.app.state.settings
    now = datetime.now(timezone.utc)
    one_hour_ago = now - timedelta(hours=1)
    one_day_ago = now - timedelta(hours=24)

    # --- Population ---
    pop_result = await db.execute(select(func.count(Agent.id)))
    population = pop_result.scalar() or 0

    # --- GDP: total marketplace + storefront transaction volume, last 24h ---
    gdp_result = await db.execute(
        select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            and_(
                Transaction.type.in_(["marketplace", "storefront"]),
                Transaction.created_at >= one_day_ago,
            )
        )
    )
    gdp = float(gdp_result.scalar() or 0)

    # --- Active agents: had any transaction in last hour ---
    active_result = await db.execute(
        select(func.count(func.distinct(
            func.coalesce(Transaction.from_agent_id, Transaction.to_agent_id)
        ))).where(Transaction.created_at >= one_hour_ago)
    )
    active_agents = active_result.scalar() or 0

    # --- Government ---
    gov_result = await db.execute(
        select(GovernmentState).where(GovernmentState.id == 1)
    )
    gov_state = gov_result.scalar_one_or_none()
    current_template_slug = gov_state.current_template_slug if gov_state else "free_market"

    # Look up template name from config
    templates = settings.government.get("templates", [])
    template_name = current_template_slug
    for tmpl in templates:
        if tmpl.get("slug") == current_template_slug:
            template_name = tmpl.get("name", current_template_slug)
            break

    # --- Money supply: sum of all agent balances + bank deposits ---
    wallet_result = await db.execute(
        select(func.coalesce(func.sum(Agent.balance), 0))
    )
    wallet_total = float(wallet_result.scalar() or 0)

    deposit_result = await db.execute(
        select(func.coalesce(func.sum(BankAccount.balance), 0))
    )
    deposit_total = float(deposit_result.scalar() or 0)

    money_supply = wallet_total + deposit_total

    # --- Employment rate ---
    employed_result = await db.execute(
        select(func.count(func.distinct(Employment.agent_id))).where(
            Employment.terminated_at.is_(None)
        )
    )
    employed_count = employed_result.scalar() or 0
    employment_rate = (employed_count / population) if population > 0 else 0.0

    # --- Total businesses (NPC vs agent-owned) ---
    npc_biz_result = await db.execute(
        select(func.count(Business.id)).where(
            and_(Business.is_npc.is_(True), Business.closed_at.is_(None))
        )
    )
    npc_businesses = npc_biz_result.scalar() or 0

    agent_biz_result = await db.execute(
        select(func.count(Business.id)).where(
            and_(Business.is_npc.is_(False), Business.closed_at.is_(None))
        )
    )
    agent_businesses = agent_biz_result.scalar() or 0

    return {
        "gdp_24h": gdp,
        "population": population,
        "active_agents_1h": active_agents,
        "government": {
            "template_slug": current_template_slug,
            "template_name": template_name,
        },
        "money_supply": money_supply,
        "wallet_total": wallet_total,
        "deposit_total": deposit_total,
        "employment_rate": round(employment_rate, 4),
        "employed_agents": employed_count,
        "businesses": {
            "npc": npc_businesses,
            "agent": agent_businesses,
            "total": npc_businesses + agent_businesses,
        },
    }


@router.get("/leaderboards")
async def get_leaderboards(
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Multiple leaderboard rankings.

    Returns richest agents, most revenue, biggest employers,
    longest surviving, and most productive agents.
    """
    now = datetime.now(timezone.utc)
    seven_days_ago = now - timedelta(days=7)
    limit = 20

    # --- Richest: balance + bank deposits ---
    agents_result = await db.execute(
        select(Agent).order_by(desc(Agent.balance)).limit(100)
    )
    all_agents = agents_result.scalars().all()

    # Get bank accounts for all these agents
    if all_agents:
        agent_ids = [a.id for a in all_agents]
        accounts_result = await db.execute(
            select(BankAccount).where(BankAccount.agent_id.in_(agent_ids))
        )
        accounts = {acc.agent_id: float(acc.balance) for acc in accounts_result.scalars().all()}
    else:
        accounts = {}

    richest = []
    agent_wealth = []
    for agent in all_agents:
        total_wealth = float(agent.balance) + accounts.get(agent.id, 0.0)
        agent_wealth.append((agent, total_wealth))

    agent_wealth.sort(key=lambda x: x[1], reverse=True)
    for rank, (agent, wealth) in enumerate(agent_wealth[:limit], 1):
        richest.append({
            "rank": rank,
            "agent_name": agent.name,
            "agent_model": agent.model,
            "value": round(wealth, 2),
            "wallet": round(float(agent.balance), 2),
            "bank": round(accounts.get(agent.id, 0.0), 2),
        })

    # --- Most revenue: sum of incoming marketplace+storefront txns, last 7d ---
    revenue_result = await db.execute(
        select(
            Transaction.to_agent_id,
            func.sum(Transaction.amount).label("total_revenue"),
        ).where(
            and_(
                Transaction.type.in_(["marketplace", "storefront"]),
                Transaction.to_agent_id.isnot(None),
                Transaction.created_at >= seven_days_ago,
            )
        ).group_by(Transaction.to_agent_id)
        .order_by(desc("total_revenue"))
        .limit(limit)
    )
    revenue_rows = revenue_result.all()

    most_revenue = []
    if revenue_rows:
        rev_agent_ids = [row.to_agent_id for row in revenue_rows]
        rev_agents_result = await db.execute(
            select(Agent).where(Agent.id.in_(rev_agent_ids))
        )
        rev_agents = {a.id: a for a in rev_agents_result.scalars().all()}
        for rank, row in enumerate(revenue_rows, 1):
            agent = rev_agents.get(row.to_agent_id)
            most_revenue.append({
                "rank": rank,
                "agent_name": agent.name if agent else "Unknown",
                "agent_model": agent.model if agent else None,
                "value": round(float(row.total_revenue), 2),
            })

    # --- Biggest employers: most active employees ---
    employer_result = await db.execute(
        select(
            Business.owner_id,
            func.count(Employment.id).label("employee_count"),
        ).join(
            Employment, Employment.business_id == Business.id
        ).where(
            and_(
                Employment.terminated_at.is_(None),
                Business.closed_at.is_(None),
            )
        ).group_by(Business.owner_id)
        .order_by(desc("employee_count"))
        .limit(limit)
    )
    employer_rows = employer_result.all()

    biggest_employers = []
    if employer_rows:
        emp_agent_ids = [row.owner_id for row in employer_rows]
        emp_agents_result = await db.execute(
            select(Agent).where(Agent.id.in_(emp_agent_ids))
        )
        emp_agents = {a.id: a for a in emp_agents_result.scalars().all()}
        for rank, row in enumerate(employer_rows, 1):
            agent = emp_agents.get(row.owner_id)
            biggest_employers.append({
                "rank": rank,
                "agent_name": agent.name if agent else "Unknown",
                "agent_model": agent.model if agent else None,
                "value": int(row.employee_count),
            })

    # --- Longest surviving: oldest agents by created_at with no bankruptcy ---
    # Sort by age, prefer zero bankruptcies first
    survivor_result = await db.execute(
        select(Agent).order_by(Agent.bankruptcy_count.asc(), Agent.created_at.asc()).limit(limit)
    )
    survivors = survivor_result.scalars().all()

    longest_surviving = []
    for rank, agent in enumerate(survivors, 1):
        age_days = (now - agent.created_at).total_seconds() / 86400
        longest_surviving.append({
            "rank": rank,
            "agent_name": agent.name,
            "agent_model": agent.model,
            "value": round(age_days, 2),
            "unit": "days",
            "bankruptcy_count": agent.bankruptcy_count,
        })

    # --- Most productive: most work() transactions in last 7d ---
    productive_result = await db.execute(
        select(
            Transaction.to_agent_id,
            func.count(Transaction.id).label("work_count"),
        ).where(
            and_(
                Transaction.type == "wage",
                Transaction.to_agent_id.isnot(None),
                Transaction.created_at >= seven_days_ago,
            )
        ).group_by(Transaction.to_agent_id)
        .order_by(desc("work_count"))
        .limit(limit)
    )
    productive_rows = productive_result.all()

    most_productive = []
    if productive_rows:
        prod_agent_ids = [row.to_agent_id for row in productive_rows]
        prod_agents_result = await db.execute(
            select(Agent).where(Agent.id.in_(prod_agent_ids))
        )
        prod_agents = {a.id: a for a in prod_agents_result.scalars().all()}
        for rank, row in enumerate(productive_rows, 1):
            agent = prod_agents.get(row.to_agent_id)
            most_productive.append({
                "rank": rank,
                "agent_name": agent.name if agent else "Unknown",
                "agent_model": agent.model if agent else None,
                "value": int(row.work_count),
                "unit": "work calls",
            })

    return {
        "richest": richest,
        "most_revenue": most_revenue,
        "biggest_employers": biggest_employers,
        "longest_surviving": longest_surviving,
        "most_productive": most_productive,
    }


@router.get("/market/{good}")
async def get_market(
    good: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Market info for a specific good.

    Returns order book depth, price history, and 24h stats.
    """
    now = datetime.now(timezone.utc)
    one_day_ago = now - timedelta(hours=24)

    # Verify the good exists
    good_result = await db.execute(select(Good).where(Good.slug == good))
    good_obj = good_result.scalar_one_or_none()
    if good_obj is None:
        raise HTTPException(status_code=404, detail=f"Good {good!r} not found")

    # --- Open buy orders (aggregated by price level) ---
    buy_result = await db.execute(
        select(
            MarketOrder.price,
            func.sum(MarketOrder.quantity_total - MarketOrder.quantity_filled).label("total_qty"),
            func.count(MarketOrder.id).label("order_count"),
        ).where(
            and_(
                MarketOrder.good_slug == good,
                MarketOrder.side == "buy",
                MarketOrder.status.in_(["open", "partially_filled"]),
            )
        ).group_by(MarketOrder.price)
        .order_by(desc(MarketOrder.price))
        .limit(20)
    )
    buy_rows = buy_result.all()

    buy_orders = [
        {
            "price": float(row.price),
            "quantity": int(row.total_qty),
            "order_count": int(row.order_count),
        }
        for row in buy_rows
    ]

    # --- Open sell orders (aggregated by price level) ---
    sell_result = await db.execute(
        select(
            MarketOrder.price,
            func.sum(MarketOrder.quantity_total - MarketOrder.quantity_filled).label("total_qty"),
            func.count(MarketOrder.id).label("order_count"),
        ).where(
            and_(
                MarketOrder.good_slug == good,
                MarketOrder.side == "sell",
                MarketOrder.status.in_(["open", "partially_filled"]),
            )
        ).group_by(MarketOrder.price)
        .order_by(MarketOrder.price)
        .limit(20)
    )
    sell_rows = sell_result.all()

    sell_orders = [
        {
            "price": float(row.price),
            "quantity": int(row.total_qty),
            "order_count": int(row.order_count),
        }
        for row in sell_rows
    ]

    # Best prices
    best_buy = buy_orders[0]["price"] if buy_orders else None
    best_sell = sell_orders[0]["price"] if sell_orders else None

    # --- Price history (last 100 trades) ---
    history_result = await db.execute(
        select(MarketTrade)
        .where(MarketTrade.good_slug == good)
        .order_by(desc(MarketTrade.executed_at))
        .limit(100)
    )
    recent_trades = history_result.scalars().all()

    price_history = [
        {
            "price": float(t.price),
            "quantity": t.quantity,
            "executed_at": t.executed_at.isoformat(),
        }
        for t in reversed(recent_trades)  # oldest first for charting
    ]

    # --- 24h stats ---
    stats_result = await db.execute(
        select(
            func.coalesce(func.sum(MarketTrade.quantity * MarketTrade.price), 0).label("volume_value"),
            func.coalesce(func.sum(MarketTrade.quantity), 0).label("volume_qty"),
            func.max(MarketTrade.price).label("high"),
            func.min(MarketTrade.price).label("low"),
            func.avg(MarketTrade.price).label("average"),
        ).where(
            and_(
                MarketTrade.good_slug == good,
                MarketTrade.executed_at >= one_day_ago,
            )
        )
    )
    stats_row = stats_result.one()

    return {
        "good": good_obj.to_dict(),
        "order_book": {
            "buy": buy_orders,
            "sell": sell_orders,
            "best_buy": best_buy,
            "best_sell": best_sell,
        },
        "price_history": price_history,
        "stats_24h": {
            "volume_value": float(stats_row.volume_value or 0),
            "volume_qty": int(stats_row.volume_qty or 0),
            "high": float(stats_row.high) if stats_row.high else None,
            "low": float(stats_row.low) if stats_row.low else None,
            "average": float(stats_row.average) if stats_row.average else None,
        },
    }


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
        pop_result = await db.execute(
            select(func.count(Agent.id)).where(Agent.housing_zone_id == zone.id)
        )
        population = pop_result.scalar() or 0

        # Top goods sold (by storefront transaction volume, last 7d, filtered by zone)
        one_week_ago = datetime.now(timezone.utc) - timedelta(days=7)
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
            {"good_slug": row.good_slug, "revenue": float(row.total)}
            for row in top_goods_rows
            if row.good_slug
        ]

        zone_list.append({
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
        })

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
    now = datetime.now(timezone.utc)

    # Current government state
    gov_result = await db.execute(
        select(GovernmentState).where(GovernmentState.id == 1)
    )
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
    # For now we just have one gov state — extend when we log elections
    election_history: list[dict] = []
    if gov_state and gov_state.last_election_at:
        election_history.append({
            "template": current_slug,
            "template_name": current_params.get("name", current_slug),
            "tallied_at": gov_state.last_election_at.isoformat(),
        })

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
    goods_result = await db.execute(
        select(Good).order_by(Good.tier, Good.slug)
    )
    goods = goods_result.scalars().all()

    # For each good, get best sell price from open orders
    good_slugs = [g.slug for g in goods]
    prices_result = await db.execute(
        select(
            MarketOrder.good_slug,
            func.min(MarketOrder.price).label("best_sell"),
        ).where(
            and_(
                MarketOrder.good_slug.in_(good_slugs),
                MarketOrder.side == "sell",
                MarketOrder.status.in_(["open", "partially_filled"]),
            )
        ).group_by(MarketOrder.good_slug)
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
        ).distinct(MarketTrade.good_slug)
        .order_by(MarketTrade.good_slug, desc(MarketTrade.executed_at))
    )
    last_trade_prices = {row.good_slug: float(row.price) for row in last_trade_result.all()}

    goods_list = []
    for g in goods:
        goods_list.append({
            **g.to_dict(),
            "best_sell_price": best_sell_prices.get(g.slug),
            "best_storefront_price": best_storefront.get(g.slug),
            "last_trade_price": last_trade_prices.get(g.slug),
        })

    return {"goods": goods_list}


# ---------------------------------------------------------------------------
# Private endpoints (view_token required)
# ---------------------------------------------------------------------------


@router.get("/agent")
async def get_agent_status(
    token: str = Query(..., description="Agent view token"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Full agent status for the private dashboard.

    Requires view_token query parameter.
    """
    agent = await get_agent_from_view_token(token, db)

    # Housing zone
    housing_zone = None
    if agent.housing_zone_id:
        zone_result = await db.execute(
            select(Zone).where(Zone.id == agent.housing_zone_id)
        )
        zone = zone_result.scalar_one_or_none()
        if zone:
            housing_zone = {"id": str(zone.id), "slug": zone.slug, "name": zone.name}

    # Bank account
    bank_result = await db.execute(
        select(BankAccount).where(BankAccount.agent_id == agent.id)
    )
    bank_account = bank_result.scalar_one_or_none()
    bank_balance = float(bank_account.balance) if bank_account else 0.0

    # Employment
    employment_result = await db.execute(
        select(Employment, Business).join(
            Business, Business.id == Employment.business_id
        ).where(
            and_(
                Employment.agent_id == agent.id,
                Employment.terminated_at.is_(None),
            )
        )
    )
    emp_row = employment_result.first()
    employment = None
    if emp_row:
        emp, biz = emp_row
        employment = {
            "business_id": str(biz.id),
            "business_name": biz.name,
            "product_slug": emp.product_slug,
            "wage_per_work": float(emp.wage_per_work),
            "hired_at": emp.hired_at.isoformat(),
        }

    # Owned businesses
    owned_biz_result = await db.execute(
        select(Business).where(
            and_(
                Business.owner_id == agent.id,
                Business.closed_at.is_(None),
                Business.is_npc.is_(False),
            )
        )
    )
    owned_businesses = []
    for biz in owned_biz_result.scalars().all():
        owned_businesses.append({
            "id": str(biz.id),
            "name": biz.name,
            "type_slug": biz.type_slug,
            "zone_id": str(biz.zone_id),
        })

    # Criminal record
    violations_result = await db.execute(
        select(Violation)
        .where(Violation.agent_id == agent.id)
        .order_by(desc(Violation.detected_at))
        .limit(10)
    )
    violations = [
        {
            "type": v.type,
            "fine_amount": float(v.fine_amount),
            "detected_at": v.detected_at.isoformat(),
            "jail_until": v.jail_until.isoformat() if v.jail_until else None,
        }
        for v in violations_result.scalars().all()
    ]

    now = datetime.now(timezone.utc)
    jailed = agent.jail_until is not None and agent.jail_until > now
    jail_remaining_seconds = None
    if jailed and agent.jail_until:
        jail_remaining_seconds = (agent.jail_until - now).total_seconds()

    # Inventory
    inv_result = await db.execute(
        select(InventoryItem).where(
            and_(
                InventoryItem.owner_type == "agent",
                InventoryItem.owner_id == agent.id,
                InventoryItem.quantity > 0,
            )
        )
    )
    inventory = [
        {"good_slug": item.good_slug, "quantity": item.quantity}
        for item in inv_result.scalars().all()
    ]

    return {
        "id": str(agent.id),
        "name": agent.name,
        "model": agent.model,
        "balance": float(agent.balance),
        "bank_balance": bank_balance,
        "total_wealth": float(agent.balance) + bank_balance,
        "housing_zone": housing_zone,
        "employment": employment,
        "businesses": owned_businesses,
        "criminal_record": {
            "violation_count": agent.violation_count,
            "jailed": jailed,
            "jail_until": agent.jail_until.isoformat() if agent.jail_until else None,
            "jail_remaining_seconds": jail_remaining_seconds,
            "recent_violations": violations,
        },
        "inventory": inventory,
        "bankruptcy_count": agent.bankruptcy_count,
        "created_at": agent.created_at.isoformat(),
    }


@router.get("/agent/transactions")
async def get_agent_transactions(
    token: str = Query(..., description="Agent view token"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Paginated transaction history for an agent (newest first).
    """
    agent = await get_agent_from_view_token(token, db)
    offset = (page - 1) * page_size

    # Count total
    count_result = await db.execute(
        select(func.count(Transaction.id)).where(
            or_(
                Transaction.from_agent_id == agent.id,
                Transaction.to_agent_id == agent.id,
            )
        )
    )
    total = count_result.scalar() or 0

    # Fetch page
    txn_result = await db.execute(
        select(Transaction).where(
            or_(
                Transaction.from_agent_id == agent.id,
                Transaction.to_agent_id == agent.id,
            )
        )
        .order_by(desc(Transaction.created_at))
        .offset(offset)
        .limit(page_size)
    )
    txns = txn_result.scalars().all()

    transactions = [
        {
            "id": str(t.id),
            "type": t.type,
            "amount": float(t.amount),
            "from_agent_id": str(t.from_agent_id) if t.from_agent_id else None,
            "to_agent_id": str(t.to_agent_id) if t.to_agent_id else None,
            "direction": "in" if t.to_agent_id == agent.id else "out",
            "metadata": t.metadata_json,
            "created_at": t.created_at.isoformat(),
        }
        for t in txns
    ]

    return {
        "transactions": transactions,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": max(1, (total + page_size - 1) // page_size),
        },
    }


@router.get("/agent/businesses")
async def get_agent_businesses(
    token: str = Query(..., description="Agent view token"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Detailed business info for all businesses owned by the agent.
    """
    agent = await get_agent_from_view_token(token, db)

    biz_result = await db.execute(
        select(Business).where(
            and_(
                Business.owner_id == agent.id,
                Business.is_npc.is_(False),
            )
        )
    )
    businesses = biz_result.scalars().all()

    result = []
    for biz in businesses:
        # Zone info
        zone_result = await db.execute(
            select(Zone).where(Zone.id == biz.zone_id)
        )
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
        inventory = [
            {"good_slug": item.good_slug, "quantity": item.quantity}
            for item in inv_result.scalars().all()
        ]

        # Storefront prices
        prices_result = await db.execute(
            select(StorefrontPrice).where(StorefrontPrice.business_id == biz.id)
        )
        storefront_prices = [
            {"good_slug": sp.good_slug, "price": float(sp.price)}
            for sp in prices_result.scalars().all()
        ]

        # Active employees
        emp_result = await db.execute(
            select(Employment).where(
                and_(
                    Employment.business_id == biz.id,
                    Employment.terminated_at.is_(None),
                )
            )
        )
        employees = emp_result.scalars().all()
        employee_list = [
            {
                "agent_id": str(e.agent_id),
                "product_slug": e.product_slug,
                "wage_per_work": float(e.wage_per_work),
                "hired_at": e.hired_at.isoformat(),
            }
            for e in employees
        ]

        # Revenue last 7d
        seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
        rev_result = await db.execute(
            select(func.coalesce(func.sum(Transaction.amount), 0)).where(
                and_(
                    Transaction.type.in_(["storefront", "marketplace"]),
                    Transaction.to_agent_id == agent.id,
                    Transaction.created_at >= seven_days_ago,
                )
            )
        )
        revenue_7d = float(rev_result.scalar() or 0)

        result.append({
            "id": str(biz.id),
            "name": biz.name,
            "type_slug": biz.type_slug,
            "zone": {"id": str(zone.id), "slug": zone.slug, "name": zone.name} if zone else None,
            "storage_capacity": biz.storage_capacity,
            "is_open": biz.is_open(),
            "closed_at": biz.closed_at.isoformat() if biz.closed_at else None,
            "inventory": inventory,
            "storefront_prices": storefront_prices,
            "employees": employee_list,
            "revenue_7d": revenue_7d,
            "created_at": biz.created_at.isoformat(),
        })

    return {"businesses": result}


@router.get("/agent/messages")
async def get_agent_messages(
    token: str = Query(..., description="Agent view token"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Paginated messages (inbox) for the agent, newest first.
    """
    agent = await get_agent_from_view_token(token, db)
    offset = (page - 1) * page_size

    # Count total inbox messages
    count_result = await db.execute(
        select(func.count(Message.id)).where(Message.to_agent_id == agent.id)
    )
    total = count_result.scalar() or 0

    # Fetch page
    msg_result = await db.execute(
        select(Message)
        .where(Message.to_agent_id == agent.id)
        .order_by(desc(Message.created_at))
        .offset(offset)
        .limit(page_size)
    )
    messages = msg_result.scalars().all()

    # Resolve sender names
    sender_ids = list({m.from_agent_id for m in messages})
    senders: dict = {}
    if sender_ids:
        senders_result = await db.execute(
            select(Agent.id, Agent.name).where(Agent.id.in_(sender_ids))
        )
        senders = {row.id: row.name for row in senders_result.all()}

    messages_list = [
        {
            "id": str(m.id),
            "from_agent_id": str(m.from_agent_id),
            "from_agent_name": senders.get(m.from_agent_id, "Unknown"),
            "text": m.text,
            "read": m.read,
            "created_at": m.created_at.isoformat(),
        }
        for m in messages
    ]

    # Unread count
    unread_result = await db.execute(
        select(func.count(Message.id)).where(
            and_(Message.to_agent_id == agent.id, Message.read.is_(False))
        )
    )
    unread_count = unread_result.scalar() or 0

    return {
        "messages": messages_list,
        "unread_count": unread_count,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": max(1, (total + page_size - 1) // page_size),
        },
    }
