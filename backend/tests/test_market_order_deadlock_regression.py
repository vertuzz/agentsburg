"""Regression coverage for marketplace lock ordering and same-good serialization."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select

from backend.marketplace.locking import market_good_lock_key
from backend.models.agent import Agent
from backend.models.inventory import InventoryItem
from tests.conftest import give_balance, give_inventory
from tests.helpers import TestAgent


@pytest.mark.asyncio
async def test_sell_order_locks_agent_before_inventory(client, app):
    """Sell placement should block on the seller row before locking inventory."""
    seller = await TestAgent.signup(client, "market_lock_seller")
    await give_inventory(app, "market_lock_seller", "wood", 5)

    async with app.state.session_factory() as session:
        seller_row = (await session.execute(select(Agent).where(Agent.name == "market_lock_seller"))).scalar_one()
        seller_id = seller_row.id

    async with app.state.session_factory() as lock_session:
        await lock_session.execute(select(Agent).where(Agent.id == seller_id).with_for_update())

        sell_task = asyncio.create_task(
            seller.call(
                "marketplace_order",
                {"action": "sell", "product": "wood", "quantity": 1, "price": 10},
            )
        )
        try:
            await asyncio.sleep(0.2)
            assert not sell_task.done(), "sell order should be blocked by the held seller row lock"

            async with app.state.session_factory() as probe_session:
                await probe_session.execute(
                    select(InventoryItem)
                    .where(
                        InventoryItem.owner_type == "agent",
                        InventoryItem.owner_id == seller_id,
                        InventoryItem.good_slug == "wood",
                    )
                    .with_for_update(nowait=True)
                )
                await probe_session.rollback()
        finally:
            await lock_session.rollback()

        result = await asyncio.wait_for(sell_task, timeout=5)

    assert result["order"]["side"] == "sell"
    assert result["order"]["good_slug"] == "wood"


@pytest.mark.asyncio
async def test_marketplace_orders_serialize_same_good_mutations(client, app):
    """Order placement for the same good should wait on the per-good advisory lock."""
    buyer = await TestAgent.signup(client, "market_lock_buyer")
    await give_balance(app, "market_lock_buyer", 100)

    good_slug = "wood"

    async with app.state.session_factory() as lock_session:
        await lock_session.execute(select(func.pg_advisory_xact_lock(market_good_lock_key(good_slug))))

        buy_task = asyncio.create_task(
            buyer.call(
                "marketplace_order",
                {"action": "buy", "product": good_slug, "quantity": 1, "price": 10},
            )
        )
        try:
            await asyncio.sleep(0.2)
            assert not buy_task.done(), "market order should wait for the same-good advisory lock"
        finally:
            await lock_session.rollback()

        result = await asyncio.wait_for(buy_task, timeout=5)

    assert result["order"]["side"] == "buy"
    assert result["order"]["good_slug"] == good_slug
