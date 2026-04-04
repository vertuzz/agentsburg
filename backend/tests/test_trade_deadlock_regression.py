"""Regression coverage for direct-trade lock ordering."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from backend.models.agent import Agent
from backend.models.inventory import InventoryItem
from tests.conftest import give_inventory
from tests.helpers import TestAgent


@pytest.mark.asyncio
async def test_trade_proposal_locks_agent_before_inventory(client, app):
    """Trade proposal should block on the proposer row before locking inventory."""
    proposer = await TestAgent.signup(client, "trade_lock_proposer")
    await TestAgent.signup(client, "trade_lock_target")
    await give_inventory(app, "trade_lock_proposer", "wood", 5)

    async with app.state.session_factory() as session:
        proposer_row = (await session.execute(select(Agent).where(Agent.name == "trade_lock_proposer"))).scalar_one()
        proposer_id = proposer_row.id

    async with app.state.session_factory() as lock_session:
        await lock_session.execute(select(Agent).where(Agent.id == proposer_id).with_for_update())

        trade_task = asyncio.create_task(
            proposer.call(
                "trade",
                {
                    "action": "propose",
                    "target_agent": "trade_lock_target",
                    "offer_items": [{"good_slug": "wood", "quantity": 1}],
                },
            )
        )
        try:
            await asyncio.sleep(0.2)
            assert not trade_task.done(), "trade proposal should be blocked by the held proposer row lock"

            async with app.state.session_factory() as probe_session:
                await probe_session.execute(
                    select(InventoryItem)
                    .where(
                        InventoryItem.owner_type == "agent",
                        InventoryItem.owner_id == proposer_id,
                        InventoryItem.good_slug == "wood",
                    )
                    .with_for_update(nowait=True)
                )
                await probe_session.rollback()
        finally:
            await lock_session.rollback()

        result = await asyncio.wait_for(trade_task, timeout=5)

    assert result["trade"]["status"] == "pending"


@pytest.mark.asyncio
async def test_trade_cancel_locks_agent_before_returning_escrow(client, app):
    """Trade cancel should reacquire the proposer row before restoring escrow."""
    proposer = await TestAgent.signup(client, "trade_cancel_proposer")
    await TestAgent.signup(client, "trade_cancel_target")
    await give_inventory(app, "trade_cancel_proposer", "wood", 5)

    proposal = await proposer.call(
        "trade",
        {
            "action": "propose",
            "target_agent": "trade_cancel_target",
            "offer_items": [{"good_slug": "wood", "quantity": 1}],
        },
    )
    trade_id = proposal["trade"]["id"]

    async with app.state.session_factory() as session:
        proposer_row = (await session.execute(select(Agent).where(Agent.name == "trade_cancel_proposer"))).scalar_one()
        proposer_id = proposer_row.id

    async with app.state.session_factory() as lock_session:
        await lock_session.execute(select(Agent).where(Agent.id == proposer_id).with_for_update())

        cancel_task = asyncio.create_task(
            proposer.call(
                "trade",
                {
                    "action": "cancel",
                    "trade_id": trade_id,
                },
            )
        )
        try:
            await asyncio.sleep(0.2)
            assert not cancel_task.done(), "trade cancel should be blocked by the held proposer row lock"

            async with app.state.session_factory() as probe_session:
                await probe_session.execute(
                    select(InventoryItem)
                    .where(
                        InventoryItem.owner_type == "agent",
                        InventoryItem.owner_id == proposer_id,
                        InventoryItem.good_slug == "wood",
                    )
                    .with_for_update(nowait=True)
                )
                await probe_session.rollback()
        finally:
            await lock_session.rollback()

        result = await asyncio.wait_for(cancel_task, timeout=5)

    assert result["status"] == "cancelled"
