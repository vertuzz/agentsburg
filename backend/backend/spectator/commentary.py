"""
Model horse race commentary for Agent Economy.

Compares AI model performance across the economy using template-based
headlines. No LLM calls — pure aggregation and string formatting.

Redis key: spectator:commentary (10 min TTL)
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from backend.models.agent import Agent
from backend.models.banking import BankAccount

if TYPE_CHECKING:
    import redis.asyncio as aioredis
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

CACHE_KEY = "spectator:commentary"
CACHE_TTL = 600  # 10 minutes


async def generate_model_commentary(
    db: AsyncSession,
    redis: aioredis.Redis,
) -> dict:
    """
    Generate model-vs-model commentary with headline and comparisons.

    Checks Redis cache first; computes from DB on miss.
    """
    # Check cache
    cached = await redis.get(CACHE_KEY)
    if cached:
        try:
            return json.loads(cached)
        except (json.JSONDecodeError, TypeError):
            pass

    result = await _compute_commentary(db)

    # Cache result
    try:
        await redis.set(CACHE_KEY, json.dumps(result), ex=CACHE_TTL)
    except Exception:
        logger.warning("Failed to cache model commentary", exc_info=True)

    return result


async def _compute_commentary(db: AsyncSession) -> dict:
    """Query DB and build commentary data."""
    # Aggregate per-model stats
    stmt = (
        select(
            Agent.model,
            func.count(Agent.id).label("agent_count"),
            func.sum(Agent.balance + func.coalesce(BankAccount.balance, 0)).label("total_wealth"),
            func.sum(Agent.bankruptcy_count).label("total_bankruptcies"),
        )
        .outerjoin(BankAccount, BankAccount.agent_id == Agent.id)
        .where(Agent.model.isnot(None))
        .group_by(Agent.model)
    )
    result = await db.execute(stmt)
    rows = result.all()

    if not rows:
        return {"headline": "", "comparisons": [], "model_count": 0}

    # Build model stats list
    models = []
    for row in rows:
        total_wealth = float(row.total_wealth or 0)
        agent_count = int(row.agent_count or 0)
        avg_wealth = round(total_wealth / agent_count, 2) if agent_count > 0 else 0.0
        models.append(
            {
                "model": row.model,
                "total_wealth": total_wealth,
                "agent_count": agent_count,
                "avg_wealth": avg_wealth,
                "total_bankruptcies": int(row.total_bankruptcies or 0),
            }
        )

    model_count = len(models)

    if model_count == 1:
        m = models[0]
        return {
            "headline": f"Only {m['model']} agents in the economy",
            "comparisons": [],
            "model_count": 1,
        }

    # Sort by total_wealth descending to find leader/runner-up
    models.sort(key=lambda m: m["total_wealth"], reverse=True)
    leader = models[0]
    runner_up = models[1]

    # Build headline
    if runner_up["total_wealth"] > 0:
        pct_ahead = (leader["total_wealth"] - runner_up["total_wealth"]) / runner_up["total_wealth"] * 100
    else:
        pct_ahead = 100.0

    headline = (
        f"{leader['model']} agents lead with {leader['total_wealth']:,.0f} total wealth"
        f" — {pct_ahead:.0f}% ahead of {runner_up['model']}"
    )

    # Build comparisons
    comparisons = []

    # 1. Total wealth
    comparisons.append(
        _make_comparison(
            metric="total_wealth",
            models=models,
            fmt=lambda v: f"{v:,.0f}",
            text_template="{leader} agents hold {value} in total wealth, {pct:.0f}% more than {runner_up}'s {ru_value}",
        )
    )

    # 2. Agent count
    models_by_count = sorted(models, key=lambda m: m["agent_count"], reverse=True)
    comparisons.append(
        _make_comparison(
            metric="agent_count",
            models=models_by_count,
            fmt=lambda v: str(int(v)),
            text_template="{leader} has {value} agents, {pct:.0f}% more than {runner_up}'s {ru_value}",
        )
    )

    # 3. Average wealth
    models_by_avg = sorted(models, key=lambda m: m["avg_wealth"], reverse=True)
    comparisons.append(
        _make_comparison(
            metric="avg_wealth",
            models=models_by_avg,
            fmt=lambda v: f"{v:,.0f}",
            text_template="{leader} agents average {value} wealth, {pct:.0f}% more than {runner_up}'s {ru_value}",
        )
    )

    # 4. Total bankruptcies (lower is better, but we report who has most)
    models_by_bankrupt = sorted(models, key=lambda m: m["total_bankruptcies"], reverse=True)
    if models_by_bankrupt[0]["total_bankruptcies"] > 0:
        top_b = models_by_bankrupt[0]
        ru_b = models_by_bankrupt[1] if len(models_by_bankrupt) > 1 else None
        comp = {
            "metric": "total_bankruptcies",
            "leader": top_b["model"],
            "value": top_b["total_bankruptcies"],
            "runner_up": ru_b["model"] if ru_b else "",
            "runner_up_value": ru_b["total_bankruptcies"] if ru_b else 0,
            "text": (
                f"{top_b['model']} has {top_b['total_bankruptcies']} bankruptcies"
                + (f", compared to {ru_b['model']}'s {ru_b['total_bankruptcies']}" if ru_b else "")
            ),
        }
        comparisons.append(comp)

    return {
        "headline": headline,
        "comparisons": comparisons,
        "model_count": model_count,
    }


def _make_comparison(
    metric: str,
    models: list[dict],
    fmt: callable,
    text_template: str,
) -> dict:
    """Build a single comparison dict from a sorted models list."""
    leader = models[0]
    runner_up = models[1] if len(models) > 1 else None

    leader_val = leader[metric]
    ru_val = runner_up[metric] if runner_up else 0

    if ru_val > 0:
        pct = (leader_val - ru_val) / ru_val * 100
    else:
        pct = 100.0 if leader_val > 0 else 0.0

    text = text_template.format(
        leader=leader["model"],
        value=fmt(leader_val),
        pct=pct,
        runner_up=runner_up["model"] if runner_up else "",
        ru_value=fmt(ru_val),
    )

    return {
        "metric": metric,
        "leader": leader["model"],
        "value": leader_val,
        "runner_up": runner_up["model"] if runner_up else "",
        "runner_up_value": ru_val,
        "text": text,
    }
