"""Economy information handler."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

if TYPE_CHECKING:
    import redis.asyncio as aioredis
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.clock import Clock
    from backend.config import Settings
    from backend.models.agent import Agent


async def _handle_get_economy(
    params: dict,
    agent: Agent | None,
    db: AsyncSession,
    clock: Clock,
    redis: aioredis.Redis,
    settings: Settings,
) -> dict:
    """
    Query economic data for the Agent Economy world.

    section='government': Current government template, all policy parameters,
      vote counts by template, time until next election, recent violations summary.

    section='market': Price information for a specific product (delegates to
      marketplace_browse). Requires 'product' param.

    section='zones': Zone information with business counts and rent costs.

    section='stats': Aggregate economic statistics — GDP proxy (total transaction
      volume), population (agent count), money supply (sum of all balances +
      bank reserves), employment rate, government type.

    No section (default): Overview combining all sections at summary level.
    """
    section = params.get("section")
    product = params.get("product")
    zone = params.get("zone")
    page = params.get("page", 1)
    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 1

    now = clock.now()

    if section == "government":
        return await _get_economy_government(db, settings, now)
    elif section == "market":
        return await _get_economy_market(db, product, page, settings)
    elif section == "zones":
        return await _get_economy_zones(db, zone, settings)
    elif section == "stats":
        return await _get_economy_stats(db, settings, now)
    else:
        # Default: overview of everything
        return await _get_economy_overview(db, settings, now, product)


async def _get_economy_government(db: AsyncSession, settings: Settings, now) -> dict:
    """Return government section: current policy, vote counts, next election."""
    from datetime import timedelta

    from sqlalchemy import func as sqlfunc

    from backend.government.service import get_current_policy
    from backend.models.government import GovernmentState, Vote

    policy = await get_current_policy(db, settings)

    # Get GovernmentState for election timing
    state_result = await db.execute(select(GovernmentState).where(GovernmentState.id == 1))
    state = state_result.scalar_one_or_none()

    last_election = state.last_election_at if state else None
    election_interval = getattr(settings.economy, "election_interval_seconds", 604800)

    if last_election:
        next_election = last_election + timedelta(seconds=election_interval)
        seconds_until = max(0, (next_election - now).total_seconds())
    else:
        next_election = now + timedelta(seconds=election_interval)
        seconds_until = election_interval

    # Count votes by template
    votes_result = await db.execute(select(Vote.template_slug, sqlfunc.count(Vote.id)).group_by(Vote.template_slug))
    vote_counts = {slug: count for slug, count in votes_result.all()}

    # Include all templates with 0 votes
    all_templates = []
    for tmpl in settings.government.get("templates", []):
        slug = tmpl["slug"]
        all_templates.append(
            {
                "slug": slug,
                "name": tmpl.get("name", slug),
                "votes": vote_counts.get(slug, 0),
                "is_current": slug == policy.get("slug"),
                "description": tmpl.get("description", ""),
            }
        )

    return {
        "section": "government",
        "current_template": policy,
        "templates": all_templates,
        "election": {
            "last_election_at": last_election.isoformat() if last_election else None,
            "next_election_approx": next_election.isoformat(),
            "seconds_until_election": int(seconds_until),
            "total_votes_cast": sum(vote_counts.values()),
        },
        "_hints": {
            "message": (
                f"Current government: {policy.get('name', policy.get('slug'))}. "
                f"Next election in ~{seconds_until / 3600:.1f} hours. "
                "Use vote(government_type=...) to cast your vote."
            ),
        },
    }


async def _get_economy_market(db: AsyncSession, product, page: int, settings: Settings) -> dict:
    """Return market section: delegate to marketplace_browse."""
    from backend.marketplace.orderbook import browse_orders

    result = await browse_orders(
        db,
        good_slug=product,
        page=page,
        page_size=20,
        settings=settings,
    )
    return {
        "section": "market",
        **result,
        "_hints": {
            "check_back_seconds": 60,
            "message": "Prices update every minute. Use marketplace_order to place buy/sell orders.",
        },
    }


async def _get_economy_zones(db: AsyncSession, zone_slug, settings: Settings) -> dict:
    """Return zones section: zone info with business counts."""
    from sqlalchemy import func as sqlfunc

    from backend.models.business import Business
    from backend.models.zone import Zone

    if zone_slug:
        zones_result = await db.execute(select(Zone).where(Zone.slug == zone_slug))
        zones = zones_result.scalars().all()
    else:
        zones_result = await db.execute(select(Zone))
        zones = zones_result.scalars().all()

    # Count businesses per zone
    biz_counts_result = await db.execute(
        select(Business.zone_id, sqlfunc.count(Business.id))
        .where(Business.closed_at.is_(None))
        .group_by(Business.zone_id)
    )
    biz_counts = {zone_id: count for zone_id, count in biz_counts_result.all()}

    # Get government rent modifier
    from backend.government.service import get_current_policy

    policy = await get_current_policy(db, settings)
    rent_modifier = float(policy.get("rent_modifier", 1.0))

    zone_data = []
    for z in zones:
        effective_rent = float(z.rent_cost) * rent_modifier
        zone_data.append(
            {
                "slug": z.slug,
                "name": z.name,
                "base_rent_per_hour": float(z.rent_cost),
                "effective_rent_per_hour": round(effective_rent, 2),
                "foot_traffic": float(z.foot_traffic),
                "demand_multiplier": float(z.demand_multiplier),
                "active_businesses": biz_counts.get(z.id, 0),
                "allowed_business_types": z.allowed_business_types,
            }
        )

    return {
        "section": "zones",
        "zones": zone_data,
        "rent_modifier": rent_modifier,
        "_hints": {
            "check_back_seconds": 3600,
            "message": (
                "Zone rents auto-deduct hourly. Rent housing in a zone with your business to avoid commute penalty."
            ),
        },
    }


async def _get_economy_stats(db: AsyncSession, settings: Settings, now) -> dict:
    """Return aggregate economic statistics."""
    from datetime import timedelta

    from sqlalchemy import func as sqlfunc

    from backend.models.agent import Agent
    from backend.models.business import Employment
    from backend.models.transaction import Transaction

    # Population
    agent_count_result = await db.execute(select(sqlfunc.count(Agent.id)))
    agent_count = agent_count_result.scalar_one() or 0

    # Money supply: sum of all agent balances
    balance_sum_result = await db.execute(select(sqlfunc.coalesce(sqlfunc.sum(Agent.balance), 0)))
    total_agent_balances = float(balance_sum_result.scalar_one() or 0)

    # Bank reserves
    bank_reserves = 0.0
    try:
        from backend.models.banking import CentralBank

        bank_result = await db.execute(select(CentralBank).where(CentralBank.id == 1))
        bank = bank_result.scalar_one_or_none()
        if bank:
            bank_reserves = float(bank.reserves)
    except Exception:
        pass

    money_supply = total_agent_balances + bank_reserves

    # Employment rate: fraction of agents with active employment
    employed_count_result = await db.execute(
        select(sqlfunc.count(Employment.id)).where(Employment.terminated_at.is_(None))
    )
    employed_count = employed_count_result.scalar_one() or 0
    employment_rate = (employed_count / agent_count) if agent_count > 0 else 0.0

    # GDP proxy: total marketplace transaction volume in last 24h
    day_ago = now - timedelta(hours=24)
    gdp_result = await db.execute(
        select(sqlfunc.coalesce(sqlfunc.sum(Transaction.amount), 0)).where(
            Transaction.type == "marketplace",
            Transaction.created_at >= day_ago,
        )
    )
    gdp_24h = float(gdp_result.scalar_one() or 0)

    # Current government
    from backend.government.service import get_current_policy

    policy = await get_current_policy(db, settings)

    return {
        "section": "stats",
        "population": agent_count,
        "employment_rate": round(employment_rate, 3),
        "employed_agents": employed_count,
        "money_supply": round(money_supply, 2),
        "agent_wallet_total": round(total_agent_balances, 2),
        "bank_reserves": round(bank_reserves, 2),
        "gdp_24h_proxy": round(gdp_24h, 2),
        "current_government": policy.get("slug", "unknown"),
        "current_government_name": policy.get("name", "Unknown"),
        "_hints": {
            "check_back_seconds": 300,
            "message": "Stats update every minute. GDP is 24h marketplace volume.",
        },
    }


async def _get_economy_overview(db: AsyncSession, settings: Settings, now, product=None) -> dict:
    """Return a high-level overview combining all sections."""
    gov = await _get_economy_government(db, settings, now)
    stats = await _get_economy_stats(db, settings, now)

    # Minimal zone summary
    from backend.models.zone import Zone

    zones_result = await db.execute(select(Zone))
    zones = zones_result.scalars().all()
    zone_names = [z.slug for z in zones]

    # Market summary for requested product (or none)
    market = None
    if product:
        market = await _get_economy_market(db, product, 1, settings)

    return {
        "section": "overview",
        "government": {
            "current": gov["current_template"].get("slug"),
            "current_name": gov["current_template"].get("name"),
            "tax_rate": gov["current_template"].get("tax_rate"),
            "enforcement_probability": gov["current_template"].get("enforcement_probability"),
            "seconds_until_election": gov["election"]["seconds_until_election"],
            "total_votes": gov["election"]["total_votes_cast"],
        },
        "economy": {
            "population": stats["population"],
            "employment_rate": stats["employment_rate"],
            "money_supply": stats["money_supply"],
            "gdp_24h": stats["gdp_24h_proxy"],
        },
        "zones": zone_names,
        "market": market,
        "_hints": {
            "sections": ["government", "market", "zones", "stats"],
            "message": (
                "Use get_economy(section='government') for full policy details, "
                "get_economy(section='stats') for economic indicators, "
                "get_economy(section='zones') for zone info, "
                "get_economy(section='market', product='bread') for market prices."
            ),
        },
    }
