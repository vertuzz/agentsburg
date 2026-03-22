"""
Concurrency tests for the direct trading system.

Verifies that FOR UPDATE locking prevents race conditions:
1. Two concurrent propose_trade calls from the same agent cannot double-spend
2. respond_trade locks inventory to prevent TOCTOU races
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import select

from backend.models.agent import Agent
from backend.models.inventory import InventoryItem
from tests.helpers import TestAgent, ToolCallError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def give_balance(app, agent_name: str, amount: float) -> None:
    """Directly set an agent's balance for test setup."""
    async with app.state.session_factory() as session:
        result = await session.execute(select(Agent).where(Agent.name == agent_name))
        agent = result.scalar_one()
        agent.balance = Decimal(str(amount))
        await session.commit()


async def give_inventory(app, agent_name: str, good_slug: str, quantity: int) -> None:
    """Directly give an agent inventory for test setup."""
    async with app.state.session_factory() as session:
        result = await session.execute(select(Agent).where(Agent.name == agent_name))
        agent = result.scalar_one()

        inv_result = await session.execute(
            select(InventoryItem).where(
                InventoryItem.owner_type == "agent",
                InventoryItem.owner_id == agent.id,
                InventoryItem.good_slug == good_slug,
            )
        )
        inv_item = inv_result.scalar_one_or_none()
        if inv_item:
            inv_item.quantity = quantity
        else:
            session.add(InventoryItem(
                owner_type="agent",
                owner_id=agent.id,
                good_slug=good_slug,
                quantity=quantity,
            ))
        await session.commit()


async def get_balance(app, agent_name: str) -> Decimal:
    """Read an agent's current balance."""
    async with app.state.session_factory() as session:
        result = await session.execute(select(Agent).where(Agent.name == agent_name))
        agent = result.scalar_one()
        return Decimal(str(agent.balance))


async def get_inventory_qty(app, agent_name: str, good_slug: str) -> int:
    """Read an agent's inventory quantity for a given good."""
    async with app.state.session_factory() as session:
        result = await session.execute(select(Agent).where(Agent.name == agent_name))
        agent = result.scalar_one()
        inv_result = await session.execute(
            select(InventoryItem).where(
                InventoryItem.owner_type == "agent",
                InventoryItem.owner_id == agent.id,
                InventoryItem.good_slug == good_slug,
            )
        )
        inv_item = inv_result.scalar_one_or_none()
        return inv_item.quantity if inv_item else 0


# ---------------------------------------------------------------------------
# 1. Concurrent propose_trade must not double-spend balance
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_propose_trade_no_double_spend(client, app, clock, redis_client):
    """
    Two concurrent propose_trade calls from the same agent, each offering
    money that the agent can afford individually but not both together,
    should result in at most one succeeding.

    Without FOR UPDATE on the agent row, both could read the same balance,
    both pass the check, and both deduct — causing a negative balance.
    """
    proposer = await TestAgent.signup(client, "proposer_ds")
    target = await TestAgent.signup(client, "target_ds")

    # Give proposer exactly 100 — enough for one 80-money trade, not two
    await give_balance(app, "proposer_ds", 100.0)

    # Fire two concurrent propose_trade calls, each offering 80 money
    async def propose():
        return await proposer.try_call("trade", {
            "action": "propose",
            "target_agent": "target_ds",
            "offer_items": [],
            "request_items": [],
            "offer_money": 80.0,
            "request_money": 0.0,
        })

    results = await asyncio.gather(propose(), propose())

    successes = [r for r in results if r[0] is not None]
    failures = [r for r in results if r[1] is not None]

    # At most one should succeed (the other should fail with insufficient balance)
    assert len(successes) <= 1, (
        f"Both propose_trade calls succeeded — double-spend! "
        f"Results: {results}"
    )

    # Verify balance never went negative
    final_balance = await get_balance(app, "proposer_ds")
    assert final_balance >= 0, (
        f"Agent balance went negative ({final_balance}) — double-spend race condition!"
    )


# ---------------------------------------------------------------------------
# 2. Concurrent propose_trade must not double-spend inventory
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_propose_trade_no_double_spend_inventory(client, app, clock, redis_client):
    """
    Two concurrent propose_trade calls offering the same inventory items
    should result in at most one succeeding.
    """
    proposer = await TestAgent.signup(client, "proposer_inv")
    target = await TestAgent.signup(client, "target_inv")

    # Give proposer exactly 5 berries — enough for one trade of 4, not two
    await give_inventory(app, "proposer_inv", "berries", 5)
    await give_balance(app, "proposer_inv", 10.0)  # small balance for fees

    async def propose():
        return await proposer.try_call("trade", {
            "action": "propose",
            "target_agent": "target_inv",
            "offer_items": [{"good_slug": "berries", "quantity": 4}],
            "request_items": [],
            "offer_money": 0.0,
            "request_money": 0.0,
        })

    results = await asyncio.gather(propose(), propose())

    successes = [r for r in results if r[0] is not None]

    assert len(successes) <= 1, (
        f"Both propose_trade calls succeeded — inventory double-spend! "
        f"Results: {results}"
    )

    # Verify inventory didn't go negative
    remaining = await get_inventory_qty(app, "proposer_inv", "berries")
    assert remaining >= 0, (
        f"Inventory went negative ({remaining}) — race condition!"
    )


# ---------------------------------------------------------------------------
# 3. respond_trade locks inventory (TOCTOU prevention)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_respond_trade_locks_inventory(client, app, clock, redis_client):
    """
    When a target accepts a trade, their inventory should be locked with
    FOR UPDATE during the check-then-remove sequence to prevent TOCTOU.

    We test this by creating two trades requesting the same items from the
    same target, then accepting both concurrently.
    """
    proposer1 = await TestAgent.signup(client, "proposer_rt1")
    proposer2 = await TestAgent.signup(client, "proposer_rt2")
    target = await TestAgent.signup(client, "target_rt")

    # Give proposers balance for money offers
    await give_balance(app, "proposer_rt1", 200.0)
    await give_balance(app, "proposer_rt2", 200.0)

    # Give target exactly 5 berries — enough for one trade of 4, not two
    await give_inventory(app, "target_rt", "berries", 5)
    await give_balance(app, "target_rt", 10.0)

    # Proposer 1 proposes: offers 50 money, requests 4 berries
    trade1_result = await proposer1.call("trade", {
        "action": "propose",
        "target_agent": "target_rt",
        "offer_items": [],
        "request_items": [{"good_slug": "berries", "quantity": 4}],
        "offer_money": 50.0,
        "request_money": 0.0,
    })
    trade1_id = trade1_result["trade"]["id"]

    # Proposer 2 proposes: offers 50 money, requests 4 berries
    trade2_result = await proposer2.call("trade", {
        "action": "propose",
        "target_agent": "target_rt",
        "offer_items": [],
        "request_items": [{"good_slug": "berries", "quantity": 4}],
        "offer_money": 50.0,
        "request_money": 0.0,
    })
    trade2_id = trade2_result["trade"]["id"]

    # Target tries to accept both trades concurrently
    async def accept_trade(trade_id):
        return await target.try_call("trade", {
            "action": "respond",
            "trade_id": trade_id,
            "accept": True,
        })

    results = await asyncio.gather(
        accept_trade(trade1_id),
        accept_trade(trade2_id),
    )

    successes = [r for r in results if r[0] is not None]

    # At most one should succeed (the other should fail with insufficient inventory)
    assert len(successes) <= 1, (
        f"Both respond_trade calls succeeded — inventory TOCTOU race! "
        f"Results: {results}"
    )

    # Verify inventory didn't go negative
    remaining = await get_inventory_qty(app, "target_rt", "berries")
    assert remaining >= 0, (
        f"Target inventory went negative ({remaining}) — TOCTOU race condition!"
    )
