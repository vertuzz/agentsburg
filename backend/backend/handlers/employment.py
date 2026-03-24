"""Employment handlers: manage employees, list/apply jobs, work."""

from __future__ import annotations

import uuid as _uuid
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from backend.errors import (
    ALREADY_EXISTS,
    COOLDOWN_ACTIVE,
    IN_JAIL,
    INSUFFICIENT_FUNDS,
    INSUFFICIENT_INVENTORY,
    INVALID_PARAMS,
    NO_RECIPE,
    NOT_ELIGIBLE,
    NOT_EMPLOYED,
    NOT_FOUND,
    STORAGE_FULL,
    UNAUTHORIZED,
    ToolError,
)

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from backend.clock import Clock
    from backend.config import Settings
    from backend.models.agent import Agent


async def _handle_manage_employees(
    params: dict,
    agent: Agent | None,
    db: AsyncSession,
    clock: Clock,
    redis: aioredis.Redis,
    settings: Settings,
) -> dict:
    """
    Manage business workforce. Multiplexed: post_job, hire_npc, fire, quit_job, close_business.
    """
    if agent is None:
        raise ToolError(UNAUTHORIZED, "Authentication required.")

    action = params.get("action")
    valid_actions = ("post_job", "hire_npc", "fire", "quit_job", "close_business")
    if action not in valid_actions:
        raise ToolError(
            INVALID_PARAMS,
            f"Parameter 'action' must be one of: {', '.join(valid_actions)}",
        )

    # Jail check — cannot make workforce changes while jailed (except quit_job)
    if action in ("post_job", "hire_npc", "fire"):
        from backend.government.jail import check_jail

        try:
            check_jail(agent, clock)
        except ValueError as e:
            raise ToolError(IN_JAIL, str(e)) from e

    # Resolve business_id for actions that need it
    business_id = None
    if action in ("post_job", "hire_npc", "fire", "close_business"):
        business_id_str = params.get("business_id")
        if not business_id_str:
            raise ToolError(INVALID_PARAMS, f"Parameter 'business_id' is required for action='{action}'")
        try:
            business_id = _uuid.UUID(business_id_str)
        except ValueError, AttributeError:
            raise ToolError(INVALID_PARAMS, f"Invalid business_id: {business_id_str!r}")

    from backend.hints import get_pending_events

    if action == "post_job":
        title = params.get("title")
        if not title or not isinstance(title, str):
            raise ToolError(INVALID_PARAMS, "Parameter 'title' is required for post_job")

        raw_wage = params.get("wage")
        if raw_wage is None:
            raise ToolError(INVALID_PARAMS, "Parameter 'wage' is required for post_job")
        try:
            wage = float(raw_wage)
        except TypeError, ValueError:
            raise ToolError(INVALID_PARAMS, "Parameter 'wage' must be a number")

        if wage <= 0:
            raise ToolError(INVALID_PARAMS, "Parameter 'wage' must be greater than 0")
        if wage > 1_000_000:
            raise ToolError(INVALID_PARAMS, "Parameter 'wage' must be at most 1,000,000")

        product = params.get("product")
        if not product or not isinstance(product, str):
            raise ToolError(INVALID_PARAMS, "Parameter 'product' (good slug) is required for post_job")

        raw_max_workers = params.get("max_workers", 1)
        try:
            max_workers = int(raw_max_workers)
        except TypeError, ValueError:
            raise ToolError(INVALID_PARAMS, "Parameter 'max_workers' must be an integer")
        if max_workers < 1:
            raise ToolError(INVALID_PARAMS, "Parameter 'max_workers' must be at least 1")
        if max_workers > 100:
            raise ToolError(INVALID_PARAMS, "Parameter 'max_workers' must be at most 100")

        from backend.businesses.employment import post_job

        try:
            result = await post_job(
                db=db,
                agent=agent,
                business_id=business_id,
                title=title.strip(),
                wage=wage,
                product_slug=product.strip(),
                max_workers=max_workers,
            )
        except ValueError as e:
            error_msg = str(e)
            if "not found" in error_msg.lower():
                raise ToolError(NOT_FOUND, error_msg) from e
            raise ToolError(INVALID_PARAMS, error_msg) from e
        pending_events = await get_pending_events(db, agent)
        result["_hints"] = {"pending_events": pending_events, "check_back_seconds": 60}
        return result

    elif action == "hire_npc":
        from backend.businesses.employment import hire_npc_worker

        try:
            result = await hire_npc_worker(
                db=db,
                agent=agent,
                business_id=business_id,
                settings=settings,
                clock=clock,
            )
        except ValueError as e:
            error_msg = str(e)
            if "not found" in error_msg.lower():
                raise ToolError(NOT_FOUND, error_msg) from e
            elif "insufficient" in error_msg.lower():
                raise ToolError(INSUFFICIENT_FUNDS, error_msg) from e
            raise ToolError(INVALID_PARAMS, error_msg) from e
        pending_events = await get_pending_events(db, agent)
        result["_hints"] = {
            "pending_events": pending_events,
            "check_back_seconds": 60,
            "warnings": [
                "NPC workers consume business inputs when they work. "
                "Ensure your business has sufficient stock of recipe inputs, "
                "or the NPC will deplete your inventory."
            ],
        }
        return result

    elif action == "fire":
        employee_id_str = params.get("employee_id")
        if not employee_id_str:
            raise ToolError(INVALID_PARAMS, "Parameter 'employee_id' is required for action='fire'")
        try:
            employee_id = _uuid.UUID(employee_id_str)
        except ValueError, AttributeError:
            raise ToolError(INVALID_PARAMS, f"Invalid employee_id: {employee_id_str!r}")

        from backend.businesses.employment import fire_employee

        try:
            result = await fire_employee(
                db=db,
                agent=agent,
                business_id=business_id,
                employee_id=employee_id,
                clock=clock,
            )
        except ValueError as e:
            error_msg = str(e)
            if "not found" in error_msg.lower():
                raise ToolError(NOT_FOUND, error_msg) from e
            raise ToolError(INVALID_PARAMS, error_msg) from e
        pending_events = await get_pending_events(db, agent)
        result["_hints"] = {"pending_events": pending_events, "check_back_seconds": 60}
        return result

    elif action == "quit_job":
        from backend.businesses.employment import quit_job

        try:
            result = await quit_job(db=db, agent=agent, clock=clock)
        except ValueError as e:
            raise ToolError(NOT_FOUND, str(e)) from e
        pending_events = await get_pending_events(db, agent)
        result["_hints"] = {"pending_events": pending_events, "check_back_seconds": 60}
        return result

    elif action == "close_business":
        from backend.businesses.service import close_business

        try:
            result = await close_business(
                db=db,
                agent=agent,
                business_id=business_id,
                clock=clock,
            )
        except ValueError as e:
            error_msg = str(e)
            if "not found" in error_msg.lower():
                raise ToolError(NOT_FOUND, error_msg) from e
            raise ToolError(INVALID_PARAMS, error_msg) from e
        pending_events = await get_pending_events(db, agent)
        result["_hints"] = {"pending_events": pending_events, "check_back_seconds": 60}
        return result

    raise ToolError(INVALID_PARAMS, f"Unknown action: {action!r}")


async def _handle_list_jobs(
    params: dict,
    agent: Agent | None,
    db: AsyncSession,
    clock: Clock,
    redis: aioredis.Redis,
    settings: Settings,
) -> dict:
    """
    Browse available job postings with optional filters.
    """
    if agent is None:
        raise ToolError(UNAUTHORIZED, "Authentication required.")

    zone_slug = params.get("zone")
    type_slug = params.get("type")
    min_wage_raw = params.get("min_wage")
    page_raw = params.get("page", 1)

    min_wage = None
    if min_wage_raw is not None:
        try:
            min_wage = float(min_wage_raw)
        except TypeError, ValueError:
            raise ToolError(INVALID_PARAMS, "Parameter 'min_wage' must be a number")

    try:
        page = int(page_raw)
    except TypeError, ValueError:
        page = 1
    page = max(1, page)

    from backend.businesses.employment import list_jobs

    result = await list_jobs(
        db=db,
        zone_slug=zone_slug,
        type_slug=type_slug,
        min_wage=min_wage,
        page=page,
        page_size=20,
    )

    from backend.hints import get_pending_events

    pending_events = await get_pending_events(db, agent)

    return {
        **result,
        "_hints": {
            "pending_events": pending_events,
            "check_back_seconds": 60,
            "message": (f"Found {result['total']} active job postings. Use apply_job(job_id) to apply for a position."),
        },
    }


async def _handle_apply_job(
    params: dict,
    agent: Agent | None,
    db: AsyncSession,
    clock: Clock,
    redis: aioredis.Redis,
    settings: Settings,
) -> dict:
    """
    Apply for a job posting. Creates employment immediately.
    """
    if agent is None:
        raise ToolError(UNAUTHORIZED, "Authentication required.")

    from backend.government.jail import check_jail

    try:
        check_jail(agent, clock)
    except ValueError as e:
        raise ToolError(IN_JAIL, str(e)) from e

    job_id_str = params.get("job_id")
    if not job_id_str:
        raise ToolError(INVALID_PARAMS, "Parameter 'job_id' is required")

    try:
        job_id = _uuid.UUID(job_id_str)
    except ValueError, AttributeError:
        raise ToolError(INVALID_PARAMS, f"Invalid job_id: {job_id_str!r}")

    from backend.businesses.employment import apply_job

    try:
        result = await apply_job(db=db, agent=agent, job_id=job_id, clock=clock)
    except ValueError as e:
        error_msg = str(e)
        if "not found" in error_msg.lower():
            raise ToolError(NOT_FOUND, error_msg) from e
        elif "already employed" in error_msg.lower():
            raise ToolError(ALREADY_EXISTS, error_msg) from e
        elif "capacity" in error_msg.lower():
            raise ToolError(NOT_ELIGIBLE, error_msg) from e
        else:
            raise ToolError(INVALID_PARAMS, error_msg) from e

    from backend.hints import get_pending_events

    pending_events = await get_pending_events(db, agent)
    result["_hints"] = {"pending_events": pending_events, "check_back_seconds": 60}

    return result


async def _handle_work(
    params: dict,
    agent: Agent | None,
    db: AsyncSession,
    clock: Clock,
    redis: aioredis.Redis,
    settings: Settings,
) -> dict:
    """
    Perform one unit of production work.

    Routes automatically: employed → produce for employer (and earn wage);
    self-employed → produce for own business inventory.
    """
    if agent is None:
        raise ToolError(UNAUTHORIZED, "Authentication required.")

    from backend.government.jail import check_jail

    try:
        check_jail(agent, clock)
    except ValueError as e:
        raise ToolError(IN_JAIL, str(e)) from e

    from backend.businesses.production import work

    business_id = params.get("business_id")

    try:
        result = await work(
            db=db,
            redis=redis,
            agent=agent,
            clock=clock,
            settings=settings,
            business_id=business_id,
        )
    except ValueError as e:
        error_msg = str(e)
        if "cooldown active" in error_msg.lower():
            raise ToolError(COOLDOWN_ACTIVE, error_msg) from e
        elif "not employed" in error_msg.lower() or "no open business" in error_msg.lower():
            raise ToolError(NOT_EMPLOYED, error_msg) from e
        elif "lacks inputs" in error_msg.lower():
            raise ToolError(INSUFFICIENT_INVENTORY, error_msg) from e
        elif "storage" in error_msg.lower():
            raise ToolError(STORAGE_FULL, error_msg) from e
        elif "no recipe" in error_msg.lower():
            raise ToolError(NO_RECIPE, error_msg) from e
        elif "jailed" in error_msg.lower():
            raise ToolError(IN_JAIL, error_msg) from e
        else:
            raise ToolError(INVALID_PARAMS, error_msg) from e

    from backend.hints import get_pending_events

    pending_events = await get_pending_events(db, agent)
    # Work result may already have hints with cooldown_remaining
    hints = result.get("_hints", {})
    hints["pending_events"] = pending_events
    hints.setdefault("check_back_seconds", 60)
    result["_hints"] = hints

    return result
