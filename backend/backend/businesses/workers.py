"""
Worker management functions for Agent Economy.

Handles:
  - hire_npc_worker: add an NPC worker placeholder
  - fire_employee: terminate a worker
  - quit_job: worker leaves their job
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from backend.models.business import Business, Employment, JobPosting

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.clock import Clock
    from backend.config import Settings
    from backend.models.agent import Agent

logger = logging.getLogger(__name__)

# Sentinel agent_id used for NPC worker records
NPC_WORKER_SENTINEL_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


async def fire_employee(
    db: AsyncSession,
    agent: Agent,
    business_id: uuid.UUID,
    employee_id: uuid.UUID,
    clock: Clock,
) -> dict:
    """
    Terminate an employee's contract.

    Only the business owner can fire employees. The employee_id refers
    to the Employment record's id.

    Args:
        db:          Active async database session.
        agent:       The requesting agent (must be business owner).
        business_id: UUID of the business.
        employee_id: UUID of the Employment record to terminate.
        clock:       Clock for terminated_at timestamp.

    Returns:
        Dict with termination confirmation.

    Raises:
        ValueError: If not owner or employment not found.
    """
    now = clock.now()

    # Verify business ownership
    biz_result = await db.execute(select(Business).where(Business.id == business_id))
    business = biz_result.scalar_one_or_none()

    if business is None:
        raise ValueError(f"Business not found: {business_id}")

    if business.owner_id != agent.id:
        raise ValueError("You can only manage employees of your own businesses.")

    # Find the active employment
    emp_result = await db.execute(
        select(Employment).where(
            Employment.id == employee_id,
            Employment.business_id == business_id,
            Employment.terminated_at.is_(None),
        )
    )
    employment = emp_result.scalar_one_or_none()

    if employment is None:
        raise ValueError(
            f"Active employment not found: {employee_id}. The employee may have already quit or been terminated."
        )

    employment.terminated_at = now
    await db.flush()

    logger.info(
        "Business %r: terminated employee (employment_id=%s)",
        business.name,
        employee_id,
    )

    return {
        "employment_id": str(employee_id),
        "business_id": str(business_id),
        "terminated_at": now.isoformat(),
        "message": "Employee has been terminated.",
    }


async def quit_job(
    db: AsyncSession,
    agent: Agent,
    clock: Clock,
) -> dict:
    """
    Quit the agent's current job.

    Terminates the agent's active employment contract. No penalty beyond
    losing the income stream.

    Args:
        db:    Active async database session.
        agent: The quitting agent.
        clock: Clock for terminated_at timestamp.

    Returns:
        Dict with confirmation.

    Raises:
        ValueError: If agent is not currently employed.
    """
    now = clock.now()

    # Find active employment
    result = await db.execute(
        select(Employment).where(
            Employment.agent_id == agent.id,
            Employment.terminated_at.is_(None),
        )
    )
    employment = result.scalar_one_or_none()

    if employment is None:
        raise ValueError("You are not currently employed.")

    employment.terminated_at = now

    # Get business name for the response
    biz_result = await db.execute(select(Business).where(Business.id == employment.business_id))
    business = biz_result.scalar_one_or_none()
    business_name = business.name if business else "Unknown"

    await db.flush()

    logger.info(
        "Agent %s quit job at business %r (employment_id=%s)",
        agent.name,
        business_name,
        employment.id,
    )

    return {
        "employment_id": str(employment.id),
        "business_name": business_name,
        "terminated_at": now.isoformat(),
        "message": f"You have quit your job at {business_name!r}.",
    }


async def hire_npc_worker(
    db: AsyncSession,
    agent: Agent,
    business_id: uuid.UUID,
    settings: Settings,
    clock: Clock,
) -> dict:
    """
    Hire an NPC worker for a business.

    Creates an Employment record with the NPC sentinel agent ID.
    NPC workers are processed during the fast tick -- they produce goods
    automatically but at reduced efficiency and higher cost.

    Requirements:
    - Business must have an active job posting
    - Business must be below the NPC worker cap
    - NPC workers cost more than real agents (npc_worker_wage_multiplier)

    Args:
        db:          Active async database session.
        agent:       The requesting agent (must be business owner).
        business_id: UUID of the business.
        settings:    Application settings (for NPC limits and multipliers).
        clock:       Clock for hired_at timestamp.

    Returns:
        Dict with NPC employment details.

    Raises:
        ValueError: If business not found, not owner, no postings, or at NPC cap.
    """
    now = clock.now()

    # Verify business ownership
    biz_result = await db.execute(select(Business).where(Business.id == business_id))
    business = biz_result.scalar_one_or_none()

    if business is None:
        raise ValueError(f"Business not found: {business_id}")

    if business.owner_id != agent.id:
        raise ValueError("You can only hire NPC workers for your own businesses.")

    if not business.is_open():
        raise ValueError(f"Business {business.name!r} is closed.")

    # Check NPC worker cap
    npc_cap = getattr(settings.economy, "npc_worker_max_per_business", 5)
    npc_count_result = await db.execute(
        select(func.count())
        .select_from(Employment)
        .where(
            Employment.business_id == business_id,
            Employment.agent_id == NPC_WORKER_SENTINEL_ID,
            Employment.terminated_at.is_(None),
        )
    )
    npc_count = npc_count_result.scalar() or 0

    if npc_count >= npc_cap:
        raise ValueError(f"NPC worker cap reached ({npc_count}/{npc_cap}). Cannot hire more NPC workers.")

    # Find an active job posting to use for the NPC
    posting_result = await db.execute(
        select(JobPosting)
        .where(
            JobPosting.business_id == business_id,
            JobPosting.is_active.is_(True),
        )
        .limit(1)
    )
    posting = posting_result.scalar_one_or_none()

    if posting is None:
        raise ValueError(
            "No active job postings for this business. Post a job first with manage_employees(action='post_job', ...)."
        )

    # NPC wage is multiplied by the configured multiplier
    npc_wage_multiplier = getattr(settings.economy, "npc_worker_wage_multiplier", 2.0)
    npc_wage = Decimal(str(posting.wage_per_work)) * Decimal(str(npc_wage_multiplier))

    employment = Employment(
        agent_id=NPC_WORKER_SENTINEL_ID,
        business_id=business_id,
        job_posting_id=posting.id,
        wage_per_work=npc_wage,
        product_slug=posting.product_slug,
        hired_at=now,
    )
    db.add(employment)
    await db.flush()

    logger.info(
        "Business %r hired NPC worker (product=%s, npc_wage=%.2f)",
        business.name,
        posting.product_slug,
        float(npc_wage),
    )

    return {
        "employment_id": str(employment.id),
        "business_id": str(business_id),
        "business_name": business.name,
        "type": "npc",
        "product_slug": posting.product_slug,
        "npc_wage": float(npc_wage),
        "base_wage": float(posting.wage_per_work),
        "wage_multiplier": npc_wage_multiplier,
        "npc_count": npc_count + 1,
        "npc_cap": npc_cap,
        "_hints": {
            "message": (
                f"NPC worker hired at {business.name!r}. "
                f"NPC workers produce at {int(getattr(settings.economy, 'npc_worker_efficiency', 0.5) * 100)}% "
                f"efficiency and cost {npc_wage_multiplier}x the posted wage. "
                f"Prefer real agent workers when available."
            )
        },
    }
