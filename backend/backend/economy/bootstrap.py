"""
Economy bootstrap: seed reference data and singletons from YAML config at startup.

This module is responsible for ensuring the database contains the canonical
reference data defined in the YAML config files. It runs during app lifespan
and is idempotent — safe to call on every restart.

Phase 1: seed_zones — populates the zones table from zones.yaml
Phase 2: seed_goods — populates the goods table from goods.yaml
Phase 3: seed_recipes — populates the recipes table from recipes.yaml
Phase 5: seed_central_bank — creates the CentralBank singleton
Phase 6: seed_government — creates the GovernmentState singleton
Phase 7: seed_npc_businesses — creates initial NPC businesses from bootstrap.yaml
"""

from __future__ import annotations

import logging
import secrets
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select

# Re-export reference-data seeders so existing imports keep working.
from backend.economy.seeds import seed_goods, seed_recipes, seed_zones  # noqa: F401
from backend.models.business import JobPosting
from backend.models.zone import Zone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.config import Settings

logger = logging.getLogger(__name__)


async def seed_central_bank(db: AsyncSession, settings: Settings) -> None:
    """
    Create the CentralBank singleton if it doesn't exist.

    The CentralBank is a singleton with id=1. It's created once and never
    deleted. On restart, we do NOT reset reserves (that would invalidate the
    money supply invariant).

    Initial reserves come from settings.economy.initial_bank_reserves.

    Args:
        db:       Active async database session (will be flushed but not committed).
        settings: Application settings containing initial_bank_reserves.
    """
    from backend.models.banking import CentralBank

    result = await db.execute(select(CentralBank).where(CentralBank.id == 1))
    bank = result.scalar_one_or_none()

    if bank is None:
        initial_reserves = Decimal(str(settings.economy.initial_bank_reserves))
        bank = CentralBank(
            id=1,
            reserves=initial_reserves,
            total_loaned=Decimal("0"),
        )
        db.add(bank)
        await db.flush()
        logger.info(
            "Created CentralBank singleton with initial reserves: %.2f",
            float(initial_reserves),
        )
    else:
        logger.debug(
            "CentralBank already exists (reserves: %.2f, total_loaned: %.2f)",
            float(bank.reserves),
            float(bank.total_loaned),
        )


async def seed_government(db: AsyncSession, settings: Settings) -> None:
    """
    Create the GovernmentState singleton if it doesn't exist.

    The GovernmentState is a singleton with id=1. It is created once during
    bootstrap with the default template "free_market". On restart, the existing
    state is preserved (the current government does not revert on restart).

    Args:
        db:       Active async database session (will be flushed but not committed).
        settings: Application settings.
    """
    from backend.models.government import GovernmentState

    result = await db.execute(select(GovernmentState).where(GovernmentState.id == 1))
    state = result.scalar_one_or_none()

    if state is None:
        # Determine default template — first in the list, or free_market
        templates = settings.government.get("templates", [])
        default_slug = "free_market"
        if templates:
            default_slug = templates[0].get("slug", "free_market")

        state = GovernmentState(
            id=1,
            current_template_slug=default_slug,
            last_election_at=None,
        )
        db.add(state)
        await db.flush()
        logger.info(
            "Created GovernmentState singleton with default template: %s",
            default_slug,
        )
    else:
        logger.debug(
            "GovernmentState already exists (template: %s)",
            state.current_template_slug,
        )


async def seed_npc_businesses(db: AsyncSession, settings: Settings) -> None:
    """
    Create initial NPC businesses from bootstrap.yaml, if they don't exist yet.

    This is idempotent — if a business with the same name already exists, it
    is skipped. On restart, existing NPC businesses are preserved.

    For each NPC business config:
      1. Create an Agent for the NPC business owner (named e.g. "NPC_Farm_01")
      2. Create the Business (is_npc=True)
      3. Set StorefrontPrices for each item in storefront config
      4. Add initial inventory
      5. Give initial_balance (deducted from CentralBank reserves)

    Args:
        db:       Active async database session (will be flushed but not committed).
        settings: Application settings with bootstrap config.
    """
    from backend.models.agent import Agent
    from backend.models.banking import CentralBank
    from backend.models.business import Business, StorefrontPrice
    from backend.models.inventory import InventoryItem
    from backend.models.transaction import Transaction

    bootstrap_cfg = settings.bootstrap
    npc_biz_configs = bootstrap_cfg.get("npc_businesses", [])

    if not npc_biz_configs:
        logger.warning("No NPC businesses in bootstrap.yaml — skipping NPC seeding")
        return

    # Load CentralBank
    bank_result = await db.execute(select(CentralBank).where(CentralBank.id == 1))
    central_bank = bank_result.scalar_one_or_none()

    # Load zones into a dict by slug
    zones_result = await db.execute(select(Zone))
    zones_by_slug = {z.slug: z for z in zones_result.scalars().all()}

    created_count = 0
    skipped_count = 0

    for i, biz_config in enumerate(npc_biz_configs):
        biz_name = biz_config.get("name")
        if not biz_name:
            logger.warning("NPC business config missing 'name', skipping: %r", biz_config)
            continue

        # Check if a business with this name already exists
        existing_biz = await db.execute(select(Business).where(Business.name == biz_name))
        if existing_biz.scalar_one_or_none() is not None:
            skipped_count += 1
            logger.debug("NPC business %r already exists — skipping", biz_name)
            continue

        zone_slug = biz_config.get("zone", "industrial")
        zone = zones_by_slug.get(zone_slug)
        if zone is None:
            logger.warning(
                "NPC business %r references unknown zone %r — skipping",
                biz_name,
                zone_slug,
            )
            continue

        biz_type = biz_config.get("type", "workshop")
        initial_balance = Decimal(str(biz_config.get("initial_balance", 1000)))

        # Create NPC agent owner
        # Name format: "NPC_<type>_<seq>" (truncated to 64 chars)
        npc_name_base = f"NPC_{biz_type.replace('_', '').capitalize()}_{i + 1:02d}"
        # Ensure uniqueness with a random suffix
        npc_agent_name = f"{npc_name_base}_{secrets.token_hex(3)}"[:64]

        npc_agent = Agent(
            name=npc_agent_name,
            action_token=f"npc_{secrets.token_urlsafe(32)}",
            view_token=f"npc_{secrets.token_urlsafe(32)}",
            balance=float(initial_balance),
            is_npc=True,
        )
        db.add(npc_agent)
        await db.flush()  # Get npc_agent.id

        # Create the business
        business = Business(
            owner_id=npc_agent.id,
            name=biz_name,
            type_slug=biz_type,
            zone_id=zone.id,
            storage_capacity=500,
            is_npc=True,
        )
        db.add(business)
        await db.flush()  # Get business.id

        # Set storefront prices and initial inventory
        storefront_items = biz_config.get("storefront", [])
        for sf_item in storefront_items:
            good_slug = sf_item.get("good")
            price = sf_item.get("price")
            initial_stock = int(sf_item.get("initial_stock", 0))

            if not good_slug or price is None:
                continue

            # Create storefront price
            sp = StorefrontPrice(
                business_id=business.id,
                good_slug=good_slug,
                price=float(price),
            )
            db.add(sp)

            # Add initial inventory
            if initial_stock > 0:
                inv = InventoryItem(
                    owner_type="business",
                    owner_id=business.id,
                    good_slug=good_slug,
                    quantity=initial_stock,
                )
                db.add(inv)

        # Create job postings for each good this NPC business produces.
        # This lets player agents apply for jobs immediately at bootstrap.
        produces = biz_config.get("produces", [])
        default_wage = float(settings.economy.default_wage_per_work_call)
        for prod_cfg in produces:
            good_slug = prod_cfg.get("good")
            if not good_slug:
                continue
            posting = JobPosting(
                business_id=business.id,
                title=f"{good_slug.replace('_', ' ').title()} Worker",
                wage_per_work=default_wage,
                product_slug=good_slug,
                max_workers=3,
                is_active=True,
            )
            db.add(posting)

        await db.flush()

        # Deduct initial balance from CentralBank reserves (NPC loan)
        if central_bank is not None and initial_balance > 0:
            current_reserves = Decimal(str(central_bank.reserves))
            if current_reserves >= initial_balance:
                central_bank.reserves = current_reserves - initial_balance

                # Record as loan disbursement
                txn = Transaction(
                    type="loan_disbursement",
                    from_agent_id=None,
                    to_agent_id=npc_agent.id,
                    amount=float(initial_balance),
                    metadata_json={
                        "reason": "npc_bootstrap",
                        "business_name": biz_name,
                    },
                )
                db.add(txn)
            else:
                logger.warning(
                    "Insufficient bank reserves for NPC business %r (need %.2f, have %.2f)",
                    biz_name,
                    float(initial_balance),
                    float(current_reserves),
                )

        await db.flush()
        created_count += 1
        logger.info("Created NPC business: %r (zone: %s, type: %s)", biz_name, zone_slug, biz_type)

    logger.info(
        "NPC business seeding complete: %d created, %d skipped (already exist)",
        created_count,
        skipped_count,
    )
