"""Regression coverage for banking lock ordering."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from backend.models.agent import Agent
from backend.models.banking import BankAccount
from tests.conftest import give_balance
from tests.helpers import TestAgent


@pytest.mark.asyncio
async def test_bank_deposit_locks_agent_before_account(client, app):
    """Deposit should block on the agent row before locking the bank account row."""
    banker = await TestAgent.signup(client, "bank_lock_agent")
    await give_balance(app, "bank_lock_agent", 500)

    await banker.call("bank", {"action": "view_balance"})

    async with app.state.session_factory() as session:
        agent_row = (await session.execute(select(Agent).where(Agent.name == "bank_lock_agent"))).scalar_one()
        account_row = (
            await session.execute(select(BankAccount).where(BankAccount.agent_id == agent_row.id))
        ).scalar_one()
        agent_id = agent_row.id
        account_id = account_row.id

    async with app.state.session_factory() as lock_session:
        await lock_session.execute(select(Agent).where(Agent.id == agent_id).with_for_update())

        deposit_task = asyncio.create_task(
            banker.call(
                "bank",
                {
                    "action": "deposit",
                    "amount": 10,
                },
            )
        )
        try:
            await asyncio.sleep(0.2)
            assert not deposit_task.done(), "deposit should be blocked by the held agent row lock"

            async with app.state.session_factory() as probe_session:
                await probe_session.execute(
                    select(BankAccount).where(BankAccount.id == account_id).with_for_update(nowait=True)
                )
                await probe_session.rollback()
        finally:
            await lock_session.rollback()

        result = await asyncio.wait_for(deposit_task, timeout=5)

    assert result["action"] == "deposit"
    assert result["amount_deposited"] == 10.0
