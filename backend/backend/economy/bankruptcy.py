"""
Bankruptcy processing for Agent Economy.

When an agent's balance falls below the bankruptcy threshold (configurable,
default -50), the bankruptcy system triggers:

1. Liquidate all inventory at 50% of base_value (sell to bank)
2. Cancel employment (Phase 3)
3. Close businesses (Phase 3)
4. Cancel active orders/trades (Phase 4)
5. Seize bank deposits to pay down active loans, write off remainder (Phase 5)
6. If balance still negative after liquidation, zero it out
7. Increment bankruptcy_count
8. Create a bankruptcy_liquidation transaction record

The agent keeps their identity, tokens, and history. They start over
with nothing but their name and a scarred credit record.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.agent import Agent
from backend.models.business import Business, Employment
from backend.models.inventory import InventoryItem
from backend.models.transaction import Transaction

# Phase 5: banking integration
try:
    from backend.banking.service import (
        close_bank_account_for_bankruptcy,
    )

    _BANKING_AVAILABLE = True
except ImportError:
    _BANKING_AVAILABLE = False

if TYPE_CHECKING:
    from backend.clock import Clock
    from backend.config import Settings

logger = logging.getLogger(__name__)


async def process_bankruptcies(
    db: AsyncSession,
    clock: Clock,
    settings: Settings,
) -> dict:
    """
    Find and process all agents below the bankruptcy threshold.

    Args:
        db:       Active async database session.
        clock:    Clock for transaction timestamps.
        settings: Application settings.

    Returns:
        Dict with list of bankrupted agent names and summary stats.
    """
    now = clock.now()
    threshold = Decimal(str(settings.economy.bankruptcy_debt_threshold))
    liquidation_rate = Decimal(str(settings.economy.bankruptcy_liquidation_rate))

    # Find all active agents below threshold (skip already-deactivated agents)
    result = await db.execute(
        select(Agent).where(
            Agent.balance < threshold,
            Agent.is_active == True,  # noqa: E712
        )
    )
    bankrupt_agents = list(result.scalars().all())

    if not bankrupt_agents:
        return {"type": "bankruptcy", "bankrupted": [], "count": 0}

    # Build goods lookup for base_value
    goods_config = {g["slug"]: g for g in settings.goods}

    bankrupted_names = []

    for agent in bankrupt_agents:
        logger.warning(
            "Processing bankruptcy for agent %s (balance: %.2f)",
            agent.name,
            float(agent.balance),
        )

        # --- Step 1: Liquidate all inventory ---
        inv_result = await db.execute(
            select(InventoryItem).where(
                InventoryItem.owner_type == "agent",
                InventoryItem.owner_id == agent.id,
                InventoryItem.quantity > 0,
            )
        )
        inventory = list(inv_result.scalars().all())

        total_liquidated = Decimal("0")
        liquidated_items = []

        for item in inventory:
            good_data = goods_config.get(item.good_slug)
            if good_data is None:
                continue

            base_value = Decimal(str(good_data.get("base_value", 1)))
            proceeds = base_value * liquidation_rate * item.quantity
            total_liquidated += proceeds

            liquidated_items.append(
                {
                    "good_slug": item.good_slug,
                    "quantity": item.quantity,
                    "proceeds": float(proceeds),
                }
            )

            # Zero out the inventory
            item.quantity = 0

        # Credit liquidation proceeds to agent
        if total_liquidated > 0:
            agent.balance = Decimal(str(agent.balance)) + total_liquidated

            txn = Transaction(
                type="bankruptcy_liquidation",
                from_agent_id=None,  # bank pays
                to_agent_id=agent.id,
                amount=total_liquidated,
                metadata_json={
                    "items": liquidated_items,
                    "tick_time": now.isoformat(),
                },
            )
            db.add(txn)

        # --- Phase 3: Cancel employment (quit any active job) ---
        emp_result = await db.execute(
            select(Employment).where(
                Employment.agent_id == agent.id,
                Employment.terminated_at.is_(None),
            )
        )
        active_employment = emp_result.scalar_one_or_none()
        if active_employment is not None:
            active_employment.terminated_at = now
            logger.info(
                "Terminated employment for bankrupt agent %s (business %s)",
                agent.name,
                active_employment.business_id,
            )

        # --- Phase 3: Close businesses owned by this agent ---
        biz_result = await db.execute(
            select(Business).where(
                Business.owner_id == agent.id,
                Business.closed_at.is_(None),
            )
        )
        owned_businesses = list(biz_result.scalars().all())
        for biz in owned_businesses:
            biz.closed_at = now
            logger.info(
                "Closed business %r for bankrupt agent %s",
                biz.name,
                agent.name,
            )
            # Terminate all employees of this business
            biz_emp_result = await db.execute(
                select(Employment).where(
                    Employment.business_id == biz.id,
                    Employment.terminated_at.is_(None),
                )
            )
            for emp in biz_emp_result.scalars().all():
                emp.terminated_at = now

        # --- Phase 4: Cancel marketplace orders and trades ---
        # (added when marketplace module exists)
        try:
            from backend.marketplace.orderbook import cancel_agent_orders
            from backend.marketplace.trading import cancel_agent_trades

            await cancel_agent_orders(db, agent, settings)
            await cancel_agent_trades(db, agent, settings)
        except ImportError:
            pass  # Phase 4 not yet implemented

        # --- Phase 5: Seize deposits to pay loans, then write off remainder ---
        # IMPORTANT: deposits are seized FIRST to pay down loans before any
        # debt write-off, preventing the exploit: loan → deposit → default → recover.
        if _BANKING_AVAILABLE:
            await close_bank_account_for_bankruptcy(db, agent, clock)

        # --- Step 5: Zero out remaining negative balance ---
        final_balance = Decimal(str(agent.balance))
        if final_balance < 0:
            # The remaining debt is absorbed by the bank (debt forgiveness on bankruptcy)
            forgiven = abs(final_balance)
            agent.balance = Decimal("0")

            forgiveness_txn = Transaction(
                type="bankruptcy_liquidation",
                from_agent_id=None,  # bank absorbs the loss
                to_agent_id=agent.id,
                amount=forgiven,
                metadata_json={
                    "type": "debt_forgiveness",
                    "tick_time": now.isoformat(),
                },
            )
            db.add(forgiveness_txn)

        # Also evict from housing
        agent.housing_zone_id = None

        # --- Step 6: Increment bankruptcy count ---
        agent.bankruptcy_count += 1

        # --- Step 7: Deactivate agent if max bankruptcies reached ---
        max_bankruptcies = getattr(settings.economy, "max_bankruptcies_before_deactivation", 2)
        if max_bankruptcies > 0 and agent.bankruptcy_count >= max_bankruptcies:
            agent.is_active = False
            logger.warning(
                "Agent %s deactivated after %d bankruptcies",
                agent.name,
                agent.bankruptcy_count,
            )

        bankrupted_names.append(agent.name)

        logger.warning(
            "Agent %s bankrupted. Count: %d, liquidated: %.2f, final balance: 0",
            agent.name,
            agent.bankruptcy_count,
            float(total_liquidated),
        )

    await db.flush()

    return {
        "type": "bankruptcy",
        "bankrupted": bankrupted_names,
        "count": len(bankrupted_names),
    }
