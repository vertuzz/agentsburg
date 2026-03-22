"""
Government service for Agent Economy.

Handles:
  - Retrieving the current policy parameters (from the active template)
  - Vote casting (one per agent, changeable; eligibility based on agent age)
  - Weekly election tallying (count votes, apply winner, adjust loans)

Policy reads are intentionally not cached — callers always get fresh data
so government changes (after tally) take immediate effect.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import random

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.agent import Agent
from backend.models.government import GovernmentState, Vote

if TYPE_CHECKING:
    from backend.clock import Clock
    from backend.config import Settings

logger = logging.getLogger(__name__)

# Default template if GovernmentState does not exist yet
_DEFAULT_TEMPLATE = "free_market"


def get_policy_params(settings: "Settings", template_slug: str) -> dict[str, Any]:
    """
    Return the parameter dict for the given template slug.

    Looks up templates from settings.government["templates"].

    Args:
        settings:      Application settings with government.yaml parsed.
        template_slug: Slug to look up (e.g. "free_market", "authoritarian").

    Returns:
        Template parameter dict, or the free_market defaults if not found.
    """
    templates: list[dict] = settings.government.get("templates", [])
    for tmpl in templates:
        if tmpl.get("slug") == template_slug:
            return tmpl

    # Fallback to free_market
    logger.warning("Template %r not found — falling back to free_market", template_slug)
    for tmpl in templates:
        if tmpl.get("slug") == "free_market":
            return tmpl

    # Absolute fallback with sane defaults
    logger.error("free_market template also missing from config — using hardcoded defaults")
    return {
        "slug": "free_market",
        "tax_rate": 0.05,
        "enforcement_probability": 0.10,
        "interest_rate_modifier": 0.80,
        "reserve_ratio": 0.10,
        "licensing_cost_modifier": 1.0,
        "production_cooldown_modifier": 0.90,
        "rent_modifier": 1.0,
        "fine_multiplier": 1.5,
        "max_jail_seconds": 3600,
    }


async def get_current_policy(
    db: AsyncSession,
    settings: "Settings",
) -> dict[str, Any]:
    """
    Return the current government's policy parameters.

    Reads from GovernmentState if it exists, otherwise uses the default
    template (free_market). Always fetches fresh — no caching.

    Args:
        db:       Active async database session.
        settings: Application settings.

    Returns:
        Template parameter dict from government.yaml.
    """
    result = await db.execute(select(GovernmentState).where(GovernmentState.id == 1))
    state = result.scalar_one_or_none()

    slug = state.current_template_slug if state else _DEFAULT_TEMPLATE
    return get_policy_params(settings, slug)


async def cast_vote(
    db: AsyncSession,
    agent: Agent,
    template_slug: str,
    clock: "Clock",
    settings: "Settings",
) -> dict:
    """
    Cast or change a vote for a government template.

    Eligibility: agent must have existed for voting_eligibility_age_seconds
    (default 2 weeks). This prevents Sybil attacks via rapid account creation.

    Args:
        db:            Active async database session.
        agent:         The authenticated agent casting the vote.
        template_slug: The government template to vote for.
        clock:         Clock for current time.
        settings:      Application settings.

    Returns:
        Dict confirming the vote with the template name.

    Raises:
        ValueError: If the agent is not eligible or the template is invalid.
    """
    now = clock.now()

    # Validate template exists
    valid_slugs = {
        t["slug"] for t in settings.government.get("templates", [])
    }
    if template_slug not in valid_slugs:
        raise ValueError(
            f"Unknown government template {template_slug!r}. "
            f"Valid options: {', '.join(sorted(valid_slugs))}"
        )

    # Check voting eligibility: agent must be old enough
    eligibility_age = getattr(settings.economy, "voting_eligibility_age_seconds", 1_209_600)
    agent_age_seconds = (now - agent.created_at).total_seconds()

    if agent_age_seconds < eligibility_age:
        remaining = eligibility_age - agent_age_seconds
        raise ValueError(
            f"Not eligible to vote yet. You need to have existed for "
            f"{eligibility_age // 86400} days. "
            f"Remaining: {remaining / 3600:.1f} hours."
        )

    # Upsert the vote
    existing = await db.execute(
        select(Vote).where(Vote.agent_id == agent.id)
    )
    vote = existing.scalar_one_or_none()

    if vote is None:
        vote = Vote(agent_id=agent.id, template_slug=template_slug)
        db.add(vote)
        action = "cast"
    else:
        old_slug = vote.template_slug
        vote.template_slug = template_slug
        action = "changed" if old_slug != template_slug else "confirmed"

    await db.flush()

    # Get template name for display
    params = get_policy_params(settings, template_slug)

    return {
        "action": action,
        "voted_for": template_slug,
        "template_name": params.get("name", template_slug),
        "message": (
            f"Vote {action} for {params.get('name', template_slug)}. "
            "Votes are tallied weekly and the winning template takes effect immediately."
        ),
    }


async def tally_election(
    db: AsyncSession,
    clock: "Clock",
    settings: "Settings",
) -> dict:
    """
    Weekly: count eligible votes, apply the winning template.

    Only agents who have existed for voting_eligibility_age_seconds are counted.
    If no votes are cast, the current government stays.

    After tallying:
    - GovernmentState.current_template_slug is updated
    - GovernmentState.last_election_at is set to now
    - Existing active loan interest rates are adjusted by the new template's modifier
      (relative to the base rate — not compounding on previous modifiers)

    Args:
        db:       Active async database session.
        clock:    Clock for current time.
        settings: Application settings.

    Returns:
        Dict with election results (vote counts, winner, previous template).
    """
    now = clock.now()

    eligibility_age = getattr(settings.economy, "voting_eligibility_age_seconds", 1_209_600)

    # Load (or create) GovernmentState
    state_result = await db.execute(select(GovernmentState).where(GovernmentState.id == 1))
    state = state_result.scalar_one_or_none()

    previous_template = state.current_template_slug if state else _DEFAULT_TEMPLATE

    # Find all eligible agents (old enough to vote)
    # We need created_at + eligibility_age <= now
    from datetime import timedelta
    eligibility_cutoff = now - timedelta(seconds=eligibility_age)

    eligible_result = await db.execute(
        select(Agent).where(Agent.created_at <= eligibility_cutoff)
    )
    eligible_agent_ids = {a.id for a in eligible_result.scalars().all()}

    if not eligible_agent_ids:
        logger.info("Election tally: no eligible voters — government unchanged (%s)", previous_template)
        if state:
            state.last_election_at = now
        return {
            "winner": previous_template,
            "previous": previous_template,
            "changed": False,
            "total_votes": 0,
            "vote_counts": {},
            "reason": "no_eligible_voters",
        }

    # Count votes from eligible agents
    votes_result = await db.execute(
        select(Vote).where(Vote.agent_id.in_(eligible_agent_ids))
    )
    all_votes = votes_result.scalars().all()

    if not all_votes:
        logger.info("Election tally: no votes cast — government unchanged (%s)", previous_template)
        if state:
            state.last_election_at = now
        else:
            state = GovernmentState(id=1, current_template_slug=previous_template, last_election_at=now)
            db.add(state)
        await db.flush()
        return {
            "winner": previous_template,
            "previous": previous_template,
            "changed": False,
            "total_votes": 0,
            "vote_counts": {},
            "reason": "no_votes_cast",
        }

    # Tally
    vote_counts: dict[str, int] = {}
    for v in all_votes:
        vote_counts[v.template_slug] = vote_counts.get(v.template_slug, 0) + 1

    # Winner = most votes (ties broken randomly to avoid alphabetical bias)
    max_votes = max(vote_counts.values())
    tied = [slug for slug, count in vote_counts.items() if count == max_votes]
    winner = random.choice(tied)

    # Apply the new government
    changed = winner != previous_template

    if state is None:
        state = GovernmentState(id=1, current_template_slug=winner, last_election_at=now)
        db.add(state)
    else:
        state.current_template_slug = winner
        state.last_election_at = now

    # Adjust existing loan interest rates if government changed
    loans_adjusted = 0
    if changed:
        loans_adjusted = await _adjust_loan_rates(db, settings, winner)

    # Votes persist so agents don't need to re-vote every week.
    # Their last vote carries forward; they can change it anytime.

    await db.flush()

    winner_params = get_policy_params(settings, winner)

    logger.info(
        "Election tally: %d votes from %d eligible agents → winner=%s (changed=%s)",
        len(all_votes),
        len(eligible_agent_ids),
        winner,
        changed,
    )

    return {
        "winner": winner,
        "winner_name": winner_params.get("name", winner),
        "previous": previous_template,
        "changed": changed,
        "total_votes": len(all_votes),
        "vote_counts": vote_counts,
        "loans_adjusted": loans_adjusted,
        "reason": "election_held",
    }


async def _adjust_loan_rates(
    db: AsyncSession,
    settings: "Settings",
    new_template_slug: str,
) -> int:
    """
    Adjust interest rates on all active loans when the government changes.

    Recalculates each loan's installment amount using the new interest_rate_modifier.
    The outstanding principal stays the same; only future installment amounts change.
    This reflects the spec: "Government changes apply IMMEDIATELY to all existing agreements".

    Returns the count of loans adjusted.
    """
    try:
        from backend.models.banking import Loan
    except ImportError:
        return 0

    params = get_policy_params(settings, new_template_slug)
    rate_modifier = float(params.get("interest_rate_modifier", 1.0))
    base_rate = float(getattr(settings.economy, "base_loan_interest_rate", 0.05))
    new_rate = base_rate * rate_modifier

    loans_result = await db.execute(
        select(Loan).where(Loan.status == "active")
    )
    loans = loans_result.scalars().all()

    for loan in loans:
        if loan.installments_remaining <= 0:
            continue

        # remaining_balance already includes interest from the original loan,
        # so just redistribute it over the remaining installments without
        # adding new interest (which would double-charge).
        remaining = Decimal(str(loan.remaining_balance))
        new_installment = remaining / loan.installments_remaining

        loan.interest_rate = new_rate
        loan.installment_amount = new_installment

    await db.flush()
    logger.info(
        "Government change to %r: adjusted %d active loans to rate %.4f",
        new_template_slug,
        len(loans),
        new_rate,
    )
    return len(loans)
