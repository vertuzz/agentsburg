"""
Employment management for Agent Economy.

Handles the job lifecycle:
  - post_job: create a job posting
  - list_jobs: browse available positions
  - apply_job: take a job
  - fire_employee: terminate a worker
  - quit_job: worker leaves their job
  - hire_npc_worker: add an NPC worker placeholder

Employment invariants:
  - An agent can only hold one active job at a time
  - Wage is locked at time of hiring
  - Jobs have capacity limits (max_workers)
  - NPC workers are placeholder records for autonomous tick processing
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.agent import Agent
from backend.models.business import Business, Employment, JobPosting

if TYPE_CHECKING:
    from backend.clock import Clock
    from backend.config import Settings

logger = logging.getLogger(__name__)

# Sentinel agent_id used for NPC worker records
NPC_WORKER_SENTINEL_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


async def post_job(
    db: AsyncSession,
    agent: Agent,
    business_id: uuid.UUID,
    title: str,
    wage: float,
    product_slug: str,
    max_workers: int,
) -> dict:
    """
    Create a job posting for a business.

    Only the business owner can post jobs. Wage must be positive.
    Workers who find this posting can apply_job() to take it.

    Args:
        db:           Active async database session.
        agent:        The requesting agent (must be business owner).
        business_id:  UUID of the business.
        title:        Job title displayed to applicants.
        wage:         Wage per work() call in currency units.
        product_slug: Good the worker will produce.
        max_workers:  Maximum concurrent workers (1-20).

    Returns:
        Dict with job posting details.

    Raises:
        ValueError: If business not found, not owner, or invalid params.
    """
    if wage <= 0:
        raise ValueError(f"Wage must be positive, got {wage}")
    if max_workers < 1 or max_workers > 20:
        raise ValueError(f"max_workers must be 1-20, got {max_workers}")

    # Verify business ownership
    result = await db.execute(select(Business).where(Business.id == business_id))
    business = result.scalar_one_or_none()

    if business is None:
        raise ValueError(f"Business not found: {business_id}")

    if business.owner_id != agent.id:
        raise ValueError("You can only post jobs for your own businesses.")

    if not business.is_open():
        raise ValueError(f"Business {business.name!r} is closed.")

    posting = JobPosting(
        business_id=business_id,
        title=title,
        wage_per_work=Decimal(str(wage)),
        product_slug=product_slug,
        max_workers=max_workers,
        is_active=True,
    )
    db.add(posting)
    await db.flush()

    logger.info(
        "Business %r posted job %r (product=%s, wage=%.2f, max_workers=%d)",
        business.name, title, product_slug, wage, max_workers,
    )

    return {
        "job_id": str(posting.id),
        "business_id": str(business_id),
        "business_name": business.name,
        "title": title,
        "wage_per_work": float(wage),
        "product_slug": product_slug,
        "max_workers": max_workers,
        "is_active": True,
    }


async def list_jobs(
    db: AsyncSession,
    zone_slug: str | None = None,
    type_slug: str | None = None,
    min_wage: float | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """
    List active job postings with optional filters.

    Returns paginated results. Each posting includes business info.

    Args:
        db:        Active async database session.
        zone_slug: Filter by business zone (optional).
        type_slug: Filter by business type (optional).
        min_wage:  Minimum wage per work call (optional).
        page:      Page number (1-indexed).
        page_size: Results per page (max 50).

    Returns:
        Dict with items list and pagination info.
    """
    page_size = min(page_size, 50)
    offset = (page - 1) * page_size

    # Build query joining job_postings with businesses
    query = (
        select(JobPosting, Business)
        .join(Business, JobPosting.business_id == Business.id)
        .where(
            JobPosting.is_active.is_(True),
            Business.closed_at.is_(None),
        )
    )

    if min_wage is not None:
        query = query.where(JobPosting.wage_per_work >= Decimal(str(min_wage)))

    if zone_slug is not None:
        # Need to join with Zone to filter by slug
        from backend.models.zone import Zone
        query = query.join(Zone, Business.zone_id == Zone.id).where(Zone.slug == zone_slug)

    if type_slug is not None:
        query = query.where(Business.type_slug == type_slug)

    # Count total matching jobs
    count_query = select(func.count()).select_from(query.subquery())
    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0

    # Paginate
    query = query.offset(offset).limit(page_size)
    result = await db.execute(query)
    rows = result.all()

    items = []
    for posting, business in rows:
        # Count current workers for this posting
        worker_count_result = await db.execute(
            select(func.count()).select_from(Employment).where(
                Employment.job_posting_id == posting.id,
                Employment.terminated_at.is_(None),
            )
        )
        worker_count = worker_count_result.scalar() or 0

        items.append({
            **posting.to_dict(),
            "business_name": business.name,
            "business_type": business.type_slug,
            "zone_id": str(business.zone_id),
            "current_workers": worker_count,
            "slots_available": max(0, posting.max_workers - worker_count),
        })

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if total > 0 else 1,
    }


async def apply_job(
    db: AsyncSession,
    agent: Agent,
    job_id: uuid.UUID,
    clock: "Clock",
) -> dict:
    """
    Apply for a job posting.

    The agent must not already be employed. The posting must be active and
    have available capacity (current workers < max_workers).

    Wage is locked at the posting's current wage at time of hiring.

    Args:
        db:     Active async database session.
        agent:  The applying agent.
        job_id: UUID of the job posting.
        clock:  Clock for hired_at timestamp.

    Returns:
        Dict with employment details.

    Raises:
        ValueError: If posting invalid, at capacity, or agent already employed.
    """
    now = clock.now()

    # Look up the job posting
    result = await db.execute(
        select(JobPosting).where(JobPosting.id == job_id)
    )
    posting = result.scalar_one_or_none()

    if posting is None:
        raise ValueError(f"Job posting not found: {job_id}")

    if not posting.is_active:
        raise ValueError("This job posting is no longer accepting applications.")

    # Look up the business
    biz_result = await db.execute(
        select(Business).where(Business.id == posting.business_id)
    )
    business = biz_result.scalar_one_or_none()

    if business is None or not business.is_open():
        raise ValueError("The business offering this job is no longer open.")

    # Check agent is not already employed
    existing_emp = await db.execute(
        select(Employment).where(
            Employment.agent_id == agent.id,
            Employment.terminated_at.is_(None),
        )
    )
    existing = existing_emp.scalar_one_or_none()
    if existing is not None:
        raise ValueError(
            "You are already employed. Quit your current job first with "
            "manage_employees(action='quit_job')."
        )

    # Check capacity
    worker_count_result = await db.execute(
        select(func.count()).select_from(Employment).where(
            Employment.job_posting_id == posting.id,
            Employment.terminated_at.is_(None),
        )
    )
    worker_count = worker_count_result.scalar() or 0

    if worker_count >= posting.max_workers:
        raise ValueError(
            f"Job is at full capacity ({worker_count}/{posting.max_workers} workers). "
            f"Look for other openings with list_jobs()."
        )

    # Create employment record
    employment = Employment(
        agent_id=agent.id,
        business_id=posting.business_id,
        job_posting_id=posting.id,
        wage_per_work=posting.wage_per_work,
        product_slug=posting.product_slug,
        hired_at=now,
    )
    db.add(employment)
    await db.flush()

    logger.info(
        "Agent %s hired at business %r (job=%r, product=%s, wage=%.2f)",
        agent.name, business.name, posting.title, posting.product_slug,
        float(posting.wage_per_work),
    )

    return {
        "employment_id": str(employment.id),
        "job_title": posting.title,
        "business_id": str(posting.business_id),
        "business_name": business.name,
        "product_slug": posting.product_slug,
        "wage_per_work": float(posting.wage_per_work),
        "hired_at": now.isoformat(),
        "_hints": {
            "message": (
                f"You are now employed at {business.name!r} as {posting.title!r}. "
                f"Call work() to produce {posting.product_slug} and earn "
                f"{float(posting.wage_per_work):.2f} per work call."
            )
        },
    }


async def fire_employee(
    db: AsyncSession,
    agent: Agent,
    business_id: uuid.UUID,
    employee_id: uuid.UUID,
    clock: "Clock",
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
            f"Active employment not found: {employee_id}. "
            f"The employee may have already quit or been terminated."
        )

    employment.terminated_at = now
    await db.flush()

    logger.info(
        "Business %r: terminated employee (employment_id=%s)",
        business.name, employee_id,
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
    clock: "Clock",
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
    biz_result = await db.execute(
        select(Business).where(Business.id == employment.business_id)
    )
    business = biz_result.scalar_one_or_none()
    business_name = business.name if business else "Unknown"

    await db.flush()

    logger.info(
        "Agent %s quit job at business %r (employment_id=%s)",
        agent.name, business_name, employment.id,
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
    settings: "Settings",
    clock: "Clock",
) -> dict:
    """
    Hire an NPC worker for a business.

    Creates an Employment record with the NPC sentinel agent ID.
    NPC workers are processed during the fast tick — they produce goods
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
        select(func.count()).select_from(Employment).where(
            Employment.business_id == business_id,
            Employment.agent_id == NPC_WORKER_SENTINEL_ID,
            Employment.terminated_at.is_(None),
        )
    )
    npc_count = npc_count_result.scalar() or 0

    if npc_count >= npc_cap:
        raise ValueError(
            f"NPC worker cap reached ({npc_count}/{npc_cap}). "
            f"Cannot hire more NPC workers."
        )

    # Find an active job posting to use for the NPC
    posting_result = await db.execute(
        select(JobPosting).where(
            JobPosting.business_id == business_id,
            JobPosting.is_active.is_(True),
        ).limit(1)
    )
    posting = posting_result.scalar_one_or_none()

    if posting is None:
        raise ValueError(
            "No active job postings for this business. "
            "Post a job first with manage_employees(action='post_job', ...)."
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
        business.name, posting.product_slug, float(npc_wage),
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
