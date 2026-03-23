"""
Tests for agent playtest feedback fixes.

Covers: set_production bug, batch transfers, work routing, credit score,
self-employed flag, leaderboard is_npc, events, cash_on_gather, onboarding hints.
"""

import random
import string

import pytest
import pytest_asyncio

from tests.helpers import TestAgent
from tests.conftest import give_balance, give_inventory, get_inventory_qty


def _unique_name():
    suffix = "".join(random.choices(string.ascii_lowercase, k=6))
    return f"fb_{suffix}"


@pytest_asyncio.fixture
async def agent(client, app):
    """Sign up a standard test agent with a unique name."""
    name = _unique_name()
    ag = await TestAgent.signup(client, name)
    ag._test_name = name
    ag._app = app  # stash for helpers
    return ag


@pytest_asyncio.fixture
async def housed_agent(client, app, agent, clock):
    """Agent with housing + enough balance for business."""
    await give_balance(app, agent._test_name, 5000)
    await agent.call("rent_housing", {"zone": "industrial"})
    return agent


# ─────────────────────────────────────────────
# 1. set_production bug fix
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_production_stores_recipe_slug(client, housed_agent, clock):
    """configure_production should store the recipe slug, and work() should use it."""
    agent = housed_agent

    # Register a mine
    biz = await agent.call("register_business", {
        "name": "Test Mine",
        "type": "mine",
        "zone": "industrial",
    })
    biz_id = biz["business_id"]

    # Configure to produce copper_ore (extraction recipe: mine_copper)
    prod_result = await agent.call("configure_production", {
        "business_id": biz_id,
        "product": "copper_ore",
    })
    assert prod_result["selected_recipe"] == "mine_copper"

    # Work should produce copper_ore (extraction), NOT copper_ingots (smelting)
    work_result = await agent.call("work", {"business_id": biz_id})
    assert work_result["produced"]["good"] == "copper_ore"
    assert work_result["recipe_slug"] == "mine_copper"


# ─────────────────────────────────────────────
# 2. Batch inventory transfers
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_batch_deposit(client, housed_agent, clock):
    """batch_deposit should transfer multiple goods with a single cooldown."""
    agent = housed_agent

    # Give agent some goods
    await give_inventory(agent._app, agent._test_name, "wood", 5)
    await give_inventory(agent._app, agent._test_name, "stone", 3)
    await give_inventory(agent._app, agent._test_name, "berries", 10)

    # Register business
    biz = await agent.call("register_business", {
        "name": "Batch Biz",
        "type": "general_store",
        "zone": "industrial",
    })
    biz_id = biz["business_id"]

    # Batch deposit
    result = await agent.call("business_inventory", {
        "action": "batch_deposit",
        "business_id": biz_id,
        "goods": [
            {"good": "wood", "quantity": 3},
            {"good": "stone", "quantity": 2},
            {"good": "berries", "quantity": 5},
        ],
    })

    assert result["count"] == 3
    assert len(result["transferred"]) == 3
    assert result["cooldown_seconds"] == 10

    # Verify business got the goods
    clock.advance(15)  # Wait for cooldown
    view = await agent.call("business_inventory", {
        "action": "view",
        "business_id": biz_id,
    })
    inv_map = {item["good_slug"]: item["quantity"] for item in view["inventory"]}
    assert inv_map.get("wood") == 3
    assert inv_map.get("stone") == 2
    assert inv_map.get("berries") == 5


@pytest.mark.asyncio
async def test_batch_deposit_rollback_on_failure(client, housed_agent, clock):
    """If one item in batch fails, all should be rolled back."""
    agent = housed_agent

    await give_inventory(agent._app, agent._test_name, "wood", 5)
    # Don't give stone — this should cause failure

    biz = await agent.call("register_business", {
        "name": "Rollback Biz",
        "type": "general_store",
        "zone": "industrial",
    })
    biz_id = biz["business_id"]

    # Batch deposit with one impossible transfer
    _, err = await agent.try_call("business_inventory", {
        "action": "batch_deposit",
        "business_id": biz_id,
        "goods": [
            {"good": "wood", "quantity": 3},
            {"good": "stone", "quantity": 10},  # Don't have 10 stone
        ],
    })

    assert err is not None  # Should fail

    # Verify agent still has wood (rollback worked)
    qty = await get_inventory_qty(agent._app, agent._test_name, "wood")
    assert qty == 5  # Unchanged


# ─────────────────────────────────────────────
# 3. Work routing with business_id
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_work_with_business_id(client, housed_agent, clock):
    """work(business_id=X) should produce at the specified business."""
    agent = housed_agent

    # Register two businesses
    biz1 = await agent.call("register_business", {
        "name": "Mine A",
        "type": "mine",
        "zone": "industrial",
    })
    biz1_id = biz1["business_id"]

    await give_balance(agent._app, agent._test_name, 5000)
    biz2 = await agent.call("register_business", {
        "name": "Farm B",
        "type": "farm",
        "zone": "outskirts",
    })
    biz2_id = biz2["business_id"]

    # Configure different products
    await agent.call("configure_production", {
        "business_id": biz1_id,
        "product": "iron_ore",
    })
    await agent.call("configure_production", {
        "business_id": biz2_id,
        "product": "wheat",
    })

    # Work at mine specifically
    result = await agent.call("work", {"business_id": biz1_id})
    assert result["produced"]["good"] == "iron_ore"
    assert result["business_id"] == biz1_id

    # Wait for cooldown then work at farm
    clock.advance(120)
    result = await agent.call("work", {"business_id": biz2_id})
    assert result["produced"]["good"] == "wheat"
    assert result["business_id"] == biz2_id


# ─────────────────────────────────────────────
# 4. Credit score in /v1/me
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_credit_score_in_status(client, agent, clock):
    """get_status should include credit_score and max_loan_amount."""
    status = await agent.call("get_status", {})
    assert "credit_score" in status
    assert "max_loan_amount" in status
    assert isinstance(status["credit_score"], (int, float))


# ─────────────────────────────────────────────
# 5. Self-employed flag
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_self_employed_flag(client, housed_agent, clock):
    """Agent with business and no employer should show self_employed."""
    agent = housed_agent

    await agent.call("register_business", {
        "name": "Solo Biz",
        "type": "general_store",
        "zone": "industrial",
    })

    status = await agent.call("get_status", {})
    assert status["employment"]["self_employed"] is True
    assert status["employment"]["business_count"] >= 1


# ─────────────────────────────────────────────
# 6. Leaderboard is_npc flag
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_leaderboard_has_is_npc(client, agent, clock):
    """Each leaderboard entry should have an is_npc field."""
    result = await agent.call("leaderboard", {})
    assert "leaderboard" in result
    for entry in result["leaderboard"]:
        assert "is_npc" in entry
        assert isinstance(entry["is_npc"], bool)


# ─────────────────────────────────────────────
# 7. Events endpoint
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_events_endpoint(client, agent, run_tick, clock):
    """GET /v1/events should return events after a tick."""
    await give_balance(agent._app, agent._test_name, 100)
    await agent.call("rent_housing", {"zone": "outskirts"})

    # Run a tick to trigger food + rent events
    await run_tick(hours=1)

    result = await agent.call("events", {})
    assert "events" in result
    assert isinstance(result["events"], list)
    # Should have food_charged and rent_charged events
    event_types = {e["type"] for e in result["events"]}
    assert "food_charged" in event_types
    assert "rent_charged" in event_types


# ─────────────────────────────────────────────
# 8. Cash on gather (reduced from base_value)
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cash_on_gather(client, agent, clock):
    """Gathering should use cash_on_gather, not base_value."""
    result = await agent.call("gather", {"resource": "berries"})
    # berries: base_value=2, cash_on_gather=1
    assert result["cash_earned"] == 1.0
    assert result["base_value"] == 2


@pytest.mark.asyncio
async def test_cash_on_gather_copper(client, agent, clock):
    """Copper ore should pay cash_on_gather=4, not base_value=6."""
    result = await agent.call("gather", {"resource": "copper_ore"})
    assert result["cash_earned"] == 4.0
    assert result["base_value"] == 6


# ─────────────────────────────────────────────
# 9. Onboarding hints
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_onboarding_hints_new_agent(client, agent, clock):
    """New agents should get next_steps in hints."""
    status = await agent.call("get_status", {})
    hints = status.get("_hints", {})
    assert "next_steps" in hints
    tips = hints["next_steps"]
    assert len(tips) > 0
    # Should suggest housing since agent is homeless
    assert any("housing" in t.lower() for t in tips)


# ─────────────────────────────────────────────
# 10. Discard items (regression)
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_discard_items(client, agent, clock):
    """Discarding items should free storage."""
    await give_inventory(agent._app, agent._test_name, "wood", 10)

    result = await agent.call("inventory_discard", {"good": "wood", "quantity": 5})
    assert result["discarded"]["good"] == "wood"
    assert result["discarded"]["quantity"] == 5

    qty = await get_inventory_qty(agent._app, agent._test_name, "wood")
    assert qty == 5


# ─────────────────────────────────────────────
# 11. Economy events in /v1/me
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_economy_events_in_status(client, agent, clock):
    """get_status should include economy_events count."""
    status = await agent.call("get_status", {})
    assert "economy_events" in status
    assert isinstance(status["economy_events"], int)


# ─────────────────────────────────────────────
# 12. Cooldown format in /v1/me
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cooldown_format_includes_total(client, agent, clock):
    """Cooldowns should include remaining and total."""
    # Gather to create a cooldown
    await agent.call("gather", {"resource": "berries"})

    status = await agent.call("get_status", {})
    cooldowns = status.get("cooldowns", {})
    if "gather:berries" in cooldowns:
        cd = cooldowns["gather:berries"]
        assert "remaining" in cd
        assert "total" in cd
        assert cd["total"] == 25  # berries cooldown


# ─────────────────────────────────────────────
# 13. Batch withdraw
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_batch_withdraw(client, housed_agent, clock):
    """batch_withdraw should move multiple goods from business to agent."""
    agent = housed_agent

    biz = await agent.call("register_business", {
        "name": "Withdraw Biz",
        "type": "general_store",
        "zone": "industrial",
    })
    biz_id = biz["business_id"]

    # Stock the business via batch deposit
    await give_inventory(agent._app, agent._test_name, "wood", 5)
    await give_inventory(agent._app, agent._test_name, "stone", 3)

    await agent.call("business_inventory", {
        "action": "batch_deposit",
        "business_id": biz_id,
        "goods": [
            {"good": "wood", "quantity": 5},
            {"good": "stone", "quantity": 3},
        ],
    })
    clock.advance(15)  # Wait for cooldown

    # Now batch withdraw
    result = await agent.call("business_inventory", {
        "action": "batch_withdraw",
        "business_id": biz_id,
        "goods": [
            {"good": "wood", "quantity": 2},
            {"good": "stone", "quantity": 1},
        ],
    })

    assert result["count"] == 2
    assert result["action"] == "batch_withdraw"
