"""Regression coverage for /v1/work lock ordering under concurrent access."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from backend.models.agent import Agent
from backend.models.inventory import InventoryItem
from tests.conftest import give_balance, give_inventory
from tests.helpers import TestAgent


@pytest.mark.asyncio
async def test_work_locks_agents_before_inventory(client, app, clock, redis_client):
    """work() should block on agent locks before touching personal inventory rows."""
    owner = await TestAgent.signup(client, "work_lock_owner")
    worker = await TestAgent.signup(client, "work_lock_worker")

    await give_balance(app, "work_lock_owner", 5000)
    await give_balance(app, "work_lock_worker", 500)
    await give_inventory(app, "work_lock_worker", "wheat", 10)

    await owner.call("rent_housing", {"zone": "industrial"})
    await worker.call("rent_housing", {"zone": "outskirts"})

    biz = await owner.call(
        "register_business",
        {"name": "Lock Order Mill", "type": "mill", "zone": "industrial"},
    )
    await owner.call(
        "configure_production",
        {"business_id": biz["business_id"], "product": "flour"},
    )
    job = await owner.call(
        "manage_employees",
        {
            "business_id": biz["business_id"],
            "action": "post_job",
            "title": "Mill Hand",
            "wage": 5.0,
            "product": "flour",
            "max_workers": 1,
        },
    )
    await worker.call("apply_job", {"job_id": job["job_id"]})

    async with app.state.session_factory() as session:
        worker_row = (await session.execute(select(Agent).where(Agent.name == "work_lock_worker"))).scalar_one()
        worker_id = worker_row.id

    lock_key = f"lock:work:{worker_id}"

    async with app.state.session_factory() as lock_session:
        await lock_session.execute(select(Agent).where(Agent.id == worker_id).with_for_update())

        work_task = asyncio.create_task(worker.call("work", {}))
        try:
            for _ in range(40):
                if await redis_client.get(lock_key):
                    break
                await asyncio.sleep(0.05)
            else:
                pytest.fail("work() never reached its in-flight state")

            assert not work_task.done(), "work() should be blocked by the held agent row lock"

            async with app.state.session_factory() as probe_session:
                await probe_session.execute(
                    select(InventoryItem)
                    .where(
                        InventoryItem.owner_type == "agent",
                        InventoryItem.owner_id == worker_id,
                        InventoryItem.good_slug == "wheat",
                    )
                    .with_for_update(nowait=True)
                )
                await probe_session.rollback()
        finally:
            await lock_session.rollback()

        result = await asyncio.wait_for(work_task, timeout=5)

    assert result["employed"] is True
    assert result["produced"]["good"] == "flour"
    assert result["wage_earned"] == 5.0
