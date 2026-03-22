"""
Economy bootstrap: seed reference data from YAML config at startup.

This module is responsible for ensuring the database contains the canonical
reference data defined in the YAML config files. It runs during app lifespan
and is idempotent — safe to call on every restart.

Phase 1: seed_zones — populates the zones table from zones.yaml
Phase 2: seed_goods — populates the goods table from goods.yaml
Phase 3: seed_recipes — populates the recipes table from recipes.yaml
Phase 5: seed_central_bank — creates the CentralBank singleton
Phase 7: seed_npc_businesses — creates initial NPC businesses from bootstrap.yaml
"""

from __future__ import annotations

import logging
import secrets
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.zone import Zone
from backend.models.good import Good
from backend.models.recipe import Recipe
from backend.models.business import JobPosting

if TYPE_CHECKING:
    from backend.config import Settings

logger = logging.getLogger(__name__)


async def seed_zones(db: AsyncSession, settings: "Settings") -> None:
    """
    Upsert Zone records from the zones.yaml config.

    For each zone defined in settings.zones:
    - If a zone with the same slug already exists, update its properties
      (allows config changes to take effect on restart without manual SQL).
    - If no zone with that slug exists, create it.

    Args:
        db:       Active async database session (will be flushed but not committed —
                  the caller is responsible for commit).
        settings: Application settings containing the parsed zones list.
    """
    zones_config = settings.zones
    if not zones_config:
        logger.warning("No zones defined in config — zones.yaml may be missing or empty")
        return

    for zone_data in zones_config:
        slug = zone_data.get("slug")
        if not slug:
            logger.warning("Zone config entry missing 'slug', skipping: %r", zone_data)
            continue

        # Map YAML field names to model field names
        # zones.yaml uses base_rent_per_hour, foot_traffic_multiplier
        rent_cost = zone_data.get("base_rent_per_hour") or zone_data.get("rent_cost", 0)
        foot_traffic = zone_data.get("foot_traffic_multiplier") or zone_data.get("foot_traffic", 1.0)
        demand_multiplier = zone_data.get("demand_multiplier", 1.0)
        allowed_business_types = zone_data.get("allowed_business_types")  # null = all allowed

        # Fetch existing zone
        result = await db.execute(select(Zone).where(Zone.slug == slug))
        zone = result.scalar_one_or_none()

        if zone is None:
            zone = Zone(
                slug=slug,
                name=zone_data.get("name", slug),
                rent_cost=rent_cost,
                foot_traffic=foot_traffic,
                demand_multiplier=demand_multiplier,
                allowed_business_types=allowed_business_types,
            )
            db.add(zone)
            logger.info("Created zone: %s (%s)", slug, zone_data.get("name", slug))
        else:
            # Update mutable fields (slug is immutable identity)
            zone.name = zone_data.get("name", zone.name)
            zone.rent_cost = rent_cost
            zone.foot_traffic = foot_traffic
            zone.demand_multiplier = demand_multiplier
            zone.allowed_business_types = allowed_business_types
            logger.debug("Updated zone: %s", slug)

    await db.flush()
    logger.info("Zone seeding complete (%d zones processed)", len(zones_config))


async def seed_goods(db: AsyncSession, settings: "Settings") -> None:
    """
    Upsert Good records from the goods.yaml config.

    For each good defined in settings.goods:
    - If a good with the same slug already exists, update its properties.
    - If no good with that slug exists, create it.

    This is idempotent — safe to call on every restart.

    Args:
        db:       Active async database session (will be flushed but not committed).
        settings: Application settings containing the parsed goods list.
    """
    goods_config = settings.goods
    if not goods_config:
        logger.warning("No goods defined in config — goods.yaml may be missing or empty")
        return

    for good_data in goods_config:
        slug = good_data.get("slug")
        if not slug:
            logger.warning("Good config entry missing 'slug', skipping: %r", good_data)
            continue

        # Fetch existing good
        result = await db.execute(select(Good).where(Good.slug == slug))
        good = result.scalar_one_or_none()

        gather_cooldown = good_data.get("gather_cooldown_seconds")
        gatherable = bool(good_data.get("gatherable", False))

        if good is None:
            good = Good(
                slug=slug,
                name=good_data.get("name", slug),
                tier=good_data.get("tier", 1),
                storage_size=good_data.get("storage_size", 1),
                base_value=good_data.get("base_value", 1),
                gatherable=gatherable,
                gather_cooldown_seconds=gather_cooldown if gatherable else None,
            )
            db.add(good)
            logger.debug("Created good: %s (%s)", slug, good_data.get("name", slug))
        else:
            # Update mutable fields
            good.name = good_data.get("name", good.name)
            good.tier = good_data.get("tier", good.tier)
            good.storage_size = good_data.get("storage_size", good.storage_size)
            good.base_value = good_data.get("base_value", good.base_value)
            good.gatherable = gatherable
            good.gather_cooldown_seconds = gather_cooldown if gatherable else None
            logger.debug("Updated good: %s", slug)

    await db.flush()
    logger.info("Good seeding complete (%d goods processed)", len(goods_config))


async def seed_recipes(db: AsyncSession, settings: "Settings") -> None:
    """
    Upsert Recipe records from the recipes.yaml config.

    For each recipe defined in settings.recipes:
    - If a recipe with the same slug already exists, update its properties.
    - If no recipe with that slug exists, create it.

    This is idempotent — safe to call on every restart.

    Args:
        db:       Active async database session (will be flushed but not committed).
        settings: Application settings containing the parsed recipes list.
    """
    recipes_config = settings.recipes
    if not recipes_config:
        logger.warning("No recipes defined in config — recipes.yaml may be missing or empty")
        return

    for recipe_data in recipes_config:
        slug = recipe_data.get("slug")
        if not slug:
            logger.warning("Recipe config entry missing 'slug', skipping: %r", recipe_data)
            continue

        # Normalize inputs: recipes.yaml uses {good: ..., quantity: ...}
        # but the model uses inputs_json as [{good_slug: ..., quantity: ...}]
        raw_inputs = recipe_data.get("inputs", [])
        inputs_json = []
        for inp in raw_inputs:
            # Support both "good" (yaml format) and "good_slug" (DB format)
            good_slug = inp.get("good_slug") or inp.get("good")
            quantity = inp.get("quantity", 1)
            if good_slug:
                inputs_json.append({"good_slug": good_slug, "quantity": quantity})

        bonus_business_type = recipe_data.get("bonus_business_type")
        bonus_multiplier = recipe_data.get("bonus_cooldown_multiplier", 1.0)

        # Fetch existing recipe
        result = await db.execute(select(Recipe).where(Recipe.slug == slug))
        recipe = result.scalar_one_or_none()

        if recipe is None:
            recipe = Recipe(
                slug=slug,
                output_good=recipe_data.get("output_good", ""),
                output_quantity=recipe_data.get("output_quantity", 1),
                inputs_json=inputs_json,
                cooldown_seconds=recipe_data.get("cooldown_seconds", 60),
                bonus_business_type=bonus_business_type,
                bonus_cooldown_multiplier=bonus_multiplier,
            )
            db.add(recipe)
            logger.debug("Created recipe: %s", slug)
        else:
            # Update mutable fields
            recipe.output_good = recipe_data.get("output_good", recipe.output_good)
            recipe.output_quantity = recipe_data.get("output_quantity", recipe.output_quantity)
            recipe.inputs_json = inputs_json
            recipe.cooldown_seconds = recipe_data.get("cooldown_seconds", recipe.cooldown_seconds)
            recipe.bonus_business_type = bonus_business_type
            recipe.bonus_cooldown_multiplier = bonus_multiplier
            logger.debug("Updated recipe: %s", slug)

    await db.flush()
    logger.info("Recipe seeding complete (%d recipes processed)", len(recipes_config))


async def seed_central_bank(db: AsyncSession, settings: "Settings") -> None:
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


async def seed_government(db: AsyncSession, settings: "Settings") -> None:
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


async def seed_npc_businesses(db: AsyncSession, settings: "Settings") -> None:
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
        existing_biz = await db.execute(
            select(Business).where(Business.name == biz_name)
        )
        if existing_biz.scalar_one_or_none() is not None:
            skipped_count += 1
            logger.debug("NPC business %r already exists — skipping", biz_name)
            continue

        zone_slug = biz_config.get("zone", "industrial")
        zone = zones_by_slug.get(zone_slug)
        if zone is None:
            logger.warning(
                "NPC business %r references unknown zone %r — skipping",
                biz_name, zone_slug,
            )
            continue

        biz_type = biz_config.get("type", "workshop")
        initial_balance = Decimal(str(biz_config.get("initial_balance", 1000)))

        # Create NPC agent owner
        # Name format: "NPC_<type>_<seq>" (truncated to 64 chars)
        npc_name_base = f"NPC_{biz_type.replace('_', '').capitalize()}_{i+1:02d}"
        # Ensure uniqueness with a random suffix
        npc_agent_name = f"{npc_name_base}_{secrets.token_hex(3)}"[:64]

        npc_agent = Agent(
            name=npc_agent_name,
            action_token=f"npc_{secrets.token_urlsafe(32)}",
            view_token=f"npc_{secrets.token_urlsafe(32)}",
            balance=float(initial_balance),
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
                    biz_name, float(initial_balance), float(current_reserves),
                )

        await db.flush()
        created_count += 1
        logger.info("Created NPC business: %r (zone: %s, type: %s)", biz_name, zone_slug, biz_type)

    logger.info(
        "NPC business seeding complete: %d created, %d skipped (already exist)",
        created_count, skipped_count,
    )
