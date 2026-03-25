"""Business & Employment: registration, production, hiring, inventory, edge cases.

Covers:
- Business registration (mill, bakery, lumber_mill, farm, mine)
- Production configuration and bonus verification
- Storefront pricing
- Job posting, application, and employment
- Inventory transfers (deposit, withdraw, batch) with cooldown enforcement
- Edge cases: non-owner deposit, insufficient inventory, invalid actions, closed business
- Workers producing goods, wage verification, work cooldowns
- Self-employed flag and multi-business work routing
- Employee auto-deposit and NPC business auto-restock
- Employee overflow: goods go to personal inventory when business storage is full
- Extraction recipes (zero-input production)
- Inventory discard (single, bulk) with edge cases
- Batch deposit rollback on partial failure
- Storefront sale events after NPC purchases
"""

from __future__ import annotations

import uuid as _uuid

from sqlalchemy import select

from backend.models.inventory import InventoryItem
from tests.conftest import get_balance, get_inventory_qty, give_balance, give_inventory
from tests.helpers import TestAgent
from tests.simulation.helpers import print_section, print_stage


async def run_business(agents: dict[str, TestAgent], client, app, clock, run_tick, redis_client):
    """Register businesses, hire workers, produce goods, test inventory edge cases."""
    print_stage("BUSINESS & EMPLOYMENT")

    # Top up balances and re-rent housing for anyone evicted
    for name in [n for n in agents if n != "eco_homeless"]:
        await give_balance(app, name, 2000)

    housing_map = {
        "eco_miller": "industrial",
        "eco_baker": "suburbs",
        "eco_lumberjack": "industrial",
        "eco_worker1": "outskirts",
        "eco_worker2": "outskirts",
        "eco_trader": "suburbs",
        "eco_banker": "suburbs",
        "eco_politician": "suburbs",
        "eco_criminal": "outskirts",
        "eco_gatherer1": "outskirts",
        "eco_gatherer2": "outskirts",
    }
    for name, zone in housing_map.items():
        s = await agents[name].status()
        if s["housing"]["homeless"]:
            await agents[name].call("rent_housing", {"zone": zone})

    # ------------------------------------------------------------------
    # Register businesses
    # ------------------------------------------------------------------
    print_section("Registering businesses")

    mill_reg = await agents["eco_miller"].call(
        "register_business",
        {"name": "Grand Mill", "type": "mill", "zone": "industrial"},
    )
    mill_id = mill_reg["business_id"]
    assert "business_id" in mill_reg
    print(f"  Grand Mill (mill, industrial) id={mill_id[:8]}...")

    bakery_reg = await agents["eco_baker"].call(
        "register_business",
        {"name": "Sunrise Bakery", "type": "bakery", "zone": "suburbs"},
    )
    bakery_id = bakery_reg["business_id"]

    lumber_reg = await agents["eco_lumberjack"].call(
        "register_business",
        {"name": "Oak Lumber Co", "type": "lumber_mill", "zone": "industrial"},
    )
    lumber_id = lumber_reg["business_id"]
    print("  Sunrise Bakery + Oak Lumber Co registered")

    # Homeless cannot register
    _, err = await agents["eco_homeless"].try_call(
        "register_business",
        {"name": "Fail Biz", "type": "mill", "zone": "industrial"},
    )
    assert err is not None, "Homeless agent should not be able to register a business"
    print(f"  Homeless agent blocked from registration (error={err})")

    # ------------------------------------------------------------------
    # Configure production
    # ------------------------------------------------------------------
    print_section("Configuring production")

    for agent_name, biz_id, product, expect_bonus in [
        ("eco_miller", mill_id, "flour", True),
        ("eco_baker", bakery_id, "bread", True),
        ("eco_lumberjack", lumber_id, "lumber", True),
    ]:
        cfg = await agents[agent_name].call("configure_production", {"business_id": biz_id, "product": product})
        assert cfg["product_slug"] == product
        assert cfg["bonus_applies"] is expect_bonus
        print(f"  {product}: bonus={cfg['bonus_applies']}")

    # ------------------------------------------------------------------
    # Set storefront prices
    # ------------------------------------------------------------------
    print_section("Storefront prices")

    await agents["eco_miller"].call("set_prices", {"business_id": mill_id, "product": "flour", "price": 6.0})
    await agents["eco_baker"].call("set_prices", {"business_id": bakery_id, "product": "bread", "price": 10.0})
    await agents["eco_lumberjack"].call("set_prices", {"business_id": lumber_id, "product": "lumber", "price": 8.0})
    print("  flour=6, bread=10, lumber=8")

    # ------------------------------------------------------------------
    # Post jobs and hire workers
    # ------------------------------------------------------------------
    print_section("Jobs and hiring")

    for name in ["eco_miller", "eco_baker", "eco_lumberjack"]:
        await give_balance(app, name, 2000)

    mill_job = await agents["eco_miller"].call(
        "manage_employees",
        {
            "business_id": mill_id,
            "action": "post_job",
            "title": "Mill Hand",
            "wage": 5.0,
            "product": "flour",
            "max_workers": 2,
        },
    )
    mill_job_id = mill_job["job_id"]

    bakery_job = await agents["eco_baker"].call(
        "manage_employees",
        {
            "business_id": bakery_id,
            "action": "post_job",
            "title": "Baker",
            "wage": 7.0,
            "product": "bread",
            "max_workers": 2,
        },
    )
    bakery_job_id = bakery_job["job_id"]

    apply1 = await agents["eco_worker1"].call("apply_job", {"job_id": mill_job_id})
    assert "employment_id" in apply1
    apply2 = await agents["eco_worker2"].call("apply_job", {"job_id": bakery_job_id})
    assert "employment_id" in apply2
    print("  worker1 → mill, worker2 → bakery")

    jobs_list = await agents["eco_trader"].call("list_jobs", {})
    assert len(jobs_list["items"]) > 0
    print(f"  {len(jobs_list['items'])} job postings visible")

    # ------------------------------------------------------------------
    # Stock businesses via inventory transfer
    # ------------------------------------------------------------------
    print_section("Stocking businesses + transfer edge cases")

    await give_inventory(app, "eco_miller", "wheat", 60)
    await give_inventory(app, "eco_baker", "flour", 40)
    await give_inventory(app, "eco_baker", "berries", 20)
    await give_inventory(app, "eco_lumberjack", "wood", 60)

    # Successful deposit
    deposit = await agents["eco_miller"].call(
        "business_inventory",
        {"action": "deposit", "business_id": mill_id, "good": "wheat", "quantity": 30},
    )
    assert deposit["transferred"] == 30
    assert deposit["good"] == "wheat"
    assert deposit["business_storage"]["used"] > 0
    print(
        f"  Miller deposited 30 wheat (biz storage: {deposit['business_storage']['used']}/{deposit['business_storage']['capacity']})"
    )

    # Transfer cooldown enforced
    _, err = await agents["eco_miller"].try_call(
        "business_inventory",
        {"action": "deposit", "business_id": mill_id, "good": "wheat", "quantity": 10},
    )
    assert err == "COOLDOWN_ACTIVE"
    print("  Transfer cooldown enforced (30s)")

    # Edge case: non-owner cannot deposit
    await give_inventory(app, "eco_trader", "wheat", 10)
    _, err = await agents["eco_trader"].try_call(
        "business_inventory",
        {"action": "deposit", "business_id": mill_id, "good": "wheat", "quantity": 5},
    )
    assert err == "NOT_FOUND", f"Expected NOT_FOUND for non-owner deposit, got {err}"
    print("  Non-owner deposit blocked")

    # Edge case: cannot deposit more than owned
    await give_inventory(app, "eco_miller", "herbs", 3)
    clock.advance(31)
    _, err = await agents["eco_miller"].try_call(
        "business_inventory",
        {"action": "deposit", "business_id": mill_id, "good": "herbs", "quantity": 999},
    )
    assert err == "INSUFFICIENT_INVENTORY"
    print("  Insufficient inventory deposit blocked")

    # Finish stocking
    clock.advance(31)
    await agents["eco_miller"].call(
        "business_inventory",
        {"action": "deposit", "business_id": mill_id, "good": "wheat", "quantity": 30},
    )
    clock.advance(31)
    await agents["eco_baker"].call(
        "business_inventory",
        {"action": "deposit", "business_id": bakery_id, "good": "flour", "quantity": 40},
    )
    clock.advance(31)
    await agents["eco_baker"].call(
        "business_inventory",
        {"action": "deposit", "business_id": bakery_id, "good": "berries", "quantity": 20},
    )
    clock.advance(31)
    await agents["eco_lumberjack"].call(
        "business_inventory",
        {"action": "deposit", "business_id": lumber_id, "good": "wood", "quantity": 60},
    )
    print("  All businesses stocked")

    # ------------------------------------------------------------------
    # Workers produce goods
    # ------------------------------------------------------------------
    print_section("Workers producing goods")

    worker1_bal_before = await get_balance(app, "eco_worker1")
    work1 = await agents["eco_worker1"].call("work", {})
    assert work1["produced"]["good"] == "flour"
    assert work1["produced"]["quantity"] == 2
    assert work1["employed"] is True
    assert work1["wage_earned"] == 5.0
    worker1_bal_after = await get_balance(app, "eco_worker1")
    assert float(worker1_bal_after - worker1_bal_before) == 5.0
    print("  worker1: produced 2 flour, earned 5.0")

    # Work cooldown
    _, err = await agents["eco_worker1"].try_call("work", {})
    assert err == "COOLDOWN_ACTIVE"

    work2 = await agents["eco_worker2"].call("work", {})
    assert work2["produced"]["good"] == "bread"
    assert work2["produced"]["quantity"] == 3
    assert work2["wage_earned"] == 7.0
    print("  worker2: produced 3 bread, earned 7.0")

    # Self-employed owner works
    cooldown = work1["cooldown_seconds"]
    clock.advance(cooldown + 1)
    miller_work = await agents["eco_miller"].call("work", {})
    assert miller_work["produced"]["good"] == "flour"
    assert miller_work["employed"] is False
    print("  miller (self-employed): produced flour, no wage")

    clock.advance(cooldown + 1)
    lumber_work = await agents["eco_lumberjack"].call("work", {})
    assert lumber_work["produced"]["good"] == "lumber"

    # Verify business inventory via DB
    mill_uuid = _uuid.UUID(mill_id)
    async with app.state.session_factory() as session:
        flour_item = (
            await session.execute(
                select(InventoryItem).where(
                    InventoryItem.owner_type == "business",
                    InventoryItem.owner_id == mill_uuid,
                    InventoryItem.good_slug == "flour",
                )
            )
        ).scalar_one_or_none()
        flour_qty = flour_item.quantity if flour_item else 0
    assert flour_qty > 0, "Mill should have produced flour in its inventory"
    print(f"  Mill inventory: {flour_qty} flour")

    # ------------------------------------------------------------------
    # Withdraw goods + edge case: withdraw more than available
    # ------------------------------------------------------------------
    print_section("Withdrawing goods + edge cases")

    clock.advance(31)
    withdraw = await agents["eco_miller"].call(
        "business_inventory",
        {"action": "withdraw", "business_id": mill_id, "good": "flour", "quantity": 2},
    )
    assert withdraw["transferred"] == 2
    assert withdraw["action"] == "withdraw"

    # Cannot withdraw more than business has
    clock.advance(31)
    _, err = await agents["eco_miller"].try_call(
        "business_inventory",
        {"action": "withdraw", "business_id": mill_id, "good": "flour", "quantity": 99999},
    )
    assert err == "INSUFFICIENT_INVENTORY"
    print("  Withdraw + insufficient-withdraw edge case passed")

    # Invalid action
    clock.advance(31)
    _, err = await agents["eco_miller"].try_call(
        "business_inventory",
        {"action": "steal", "business_id": mill_id, "good": "wheat", "quantity": 1},
    )
    assert err == "INVALID_PARAMS"
    print("  Invalid action rejected")

    # ------------------------------------------------------------------
    # Extraction recipes (farm: zero-input production)
    # ------------------------------------------------------------------
    print_section("Extraction recipes (farm)")

    await give_balance(app, "eco_gatherer2", 500)
    s = await agents["eco_gatherer2"].status()
    if s["housing"]["homeless"]:
        await agents["eco_gatherer2"].call("rent_housing", {"zone": "outskirts"})

    farm_reg = await agents["eco_gatherer2"].call(
        "register_business",
        {"name": "Test Farm", "type": "farm", "zone": "industrial"},
    )
    farm_id = farm_reg["business_id"]

    config_farm = await agents["eco_gatherer2"].call(
        "configure_production",
        {"business_id": farm_id, "product": "wheat"},
    )
    assert config_farm["product_slug"] == "wheat"
    assert config_farm["bonus_applies"] is True

    clock.advance(120)
    farm_work = await agents["eco_gatherer2"].call("work", {})
    assert farm_work["produced"]["good"] == "wheat"
    assert farm_work["produced"]["quantity"] == 5
    assert farm_work["employed"] is False
    print(f"  Farm produced {farm_work['produced']['quantity']} wheat (zero inputs)")

    clock.advance(31)
    await agents["eco_gatherer2"].call(
        "business_inventory",
        {"action": "withdraw", "business_id": farm_id, "good": "wheat", "quantity": 2},
    )

    # ------------------------------------------------------------------
    # Inventory discard + edge cases
    # ------------------------------------------------------------------
    print_section("Inventory discard")

    await give_inventory(app, "eco_gatherer1", "stone", 10)
    g1_before = await agents["eco_gatherer1"].status()
    storage_before = g1_before["storage"]["used"]

    discard = await agents["eco_gatherer1"].call("inventory_discard", {"good": "stone", "quantity": 5})
    assert discard["discarded"]["good"] == "stone"
    assert discard["discarded"]["quantity"] == 5
    assert discard["storage"]["used"] < storage_before
    print(f"  Discarded 5 stone, storage: {discard['storage']['used']}/{discard['storage']['capacity']}")

    # Cannot discard more than owned
    clock.advance(5)
    _, err = await agents["eco_gatherer1"].try_call("inventory_discard", {"good": "stone", "quantity": 9999})
    assert err == "INSUFFICIENT_INVENTORY"

    # Cannot discard unknown good
    clock.advance(5)
    _, err = await agents["eco_gatherer1"].try_call("inventory_discard", {"good": "unobtainium", "quantity": 1})
    assert err == "INVALID_PARAMS"

    # Cannot discard zero quantity
    clock.advance(5)
    _, err = await agents["eco_gatherer1"].try_call("inventory_discard", {"good": "stone", "quantity": 0})
    assert err == "INVALID_PARAMS"
    print("  Discard edge cases: insufficient, unknown good, zero quantity — all rejected")

    # Bulk discard
    await give_inventory(app, "eco_gatherer1", "wheat", 10)
    await give_inventory(app, "eco_gatherer1", "wood", 5)
    clock.advance(10)
    bulk = await agents["eco_gatherer1"].call(
        "inventory_discard",
        {"goods": [{"good_slug": "wheat", "quantity": 3}, {"good_slug": "wood", "quantity": 2}]},
    )
    assert bulk["count"] == 2
    assert bulk["total_quantity"] == 5
    print("  Bulk discard: 2 items, 5 total quantity")

    # Bulk discard cooldown
    _, err = await agents["eco_gatherer1"].try_call(
        "inventory_discard",
        {"goods": [{"good_slug": "wheat", "quantity": 1}]},
    )
    assert err == "COOLDOWN_ACTIVE"

    # Bulk discard with unknown good
    clock.advance(10)
    _, err = await agents["eco_gatherer1"].try_call(
        "inventory_discard",
        {"goods": [{"good_slug": "unobtainium", "quantity": 1}]},
    )
    assert err == "INVALID_PARAMS"
    print("  Bulk discard edge cases passed")

    # ------------------------------------------------------------------
    # Self-employed flag
    # ------------------------------------------------------------------
    print_section("Self-employed flag")

    miller_status = await agents["eco_miller"].status()
    assert miller_status["employment"]["self_employed"] is True
    assert miller_status["employment"]["business_count"] >= 1
    print(f"  self_employed=True, business_count={miller_status['employment']['business_count']}")

    # ------------------------------------------------------------------
    # Batch inventory transfers + rollback on failure
    # ------------------------------------------------------------------
    print_section("Batch transfers + rollback")

    await give_inventory(app, "eco_gatherer2", "wood", 10)
    await give_inventory(app, "eco_gatherer2", "stone", 5)
    await give_inventory(app, "eco_gatherer2", "berries", 8)

    clock.advance(31)
    batch_dep = await agents["eco_gatherer2"].call(
        "business_inventory",
        {
            "action": "batch_deposit",
            "business_id": farm_id,
            "goods": [
                {"good": "wood", "quantity": 3},
                {"good": "stone", "quantity": 2},
                {"good": "berries", "quantity": 4},
            ],
        },
    )
    assert batch_dep["count"] == 3
    assert len(batch_dep["transferred"]) == 3
    assert batch_dep["cooldown_seconds"] == 3

    clock.advance(15)
    farm_view = await agents["eco_gatherer2"].call(
        "business_inventory",
        {"action": "view", "business_id": farm_id},
    )
    farm_inv = {item["good_slug"]: item["quantity"] for item in farm_view["inventory"]}
    assert farm_inv.get("wood", 0) >= 3
    assert farm_inv.get("stone", 0) >= 2
    assert farm_inv.get("berries", 0) >= 4
    print("  Batch deposit verified")

    # Batch withdraw
    clock.advance(15)
    batch_wd = await agents["eco_gatherer2"].call(
        "business_inventory",
        {
            "action": "batch_withdraw",
            "business_id": farm_id,
            "goods": [{"good": "wood", "quantity": 1}, {"good": "stone", "quantity": 1}],
        },
    )
    assert batch_wd["count"] == 2
    print("  Batch withdraw verified")

    # Batch deposit rollback on partial failure
    from tests.helpers import TestAgent as _TA

    rollback_owner = await _TA.signup(client, "rollback_owner")
    await give_balance(app, "rollback_owner", 1000)
    await rollback_owner.call("rent_housing", {"zone": "outskirts"})
    rollback_biz = await rollback_owner.call(
        "register_business",
        {"name": "Rollback Biz", "type": "general_store", "zone": "industrial"},
    )
    rollback_biz_id = rollback_biz["business_id"]
    await give_inventory(app, "rollback_owner", "wood", 5)

    clock.advance(31)
    _, err = await rollback_owner.try_call(
        "business_inventory",
        {
            "action": "batch_deposit",
            "business_id": rollback_biz_id,
            "goods": [{"good": "wood", "quantity": 3}, {"good": "stone", "quantity": 10}],
        },
    )
    assert err is not None, "Batch with insufficient goods should fail"
    qty = await get_inventory_qty(app, "rollback_owner", "wood")
    assert qty == 5, f"Wood should be unchanged after rollback, got {qty}"
    print("  Batch deposit rollback on partial failure verified")

    # ------------------------------------------------------------------
    # Production recipe slug persistence
    # ------------------------------------------------------------------
    print_section("Production recipe slug")

    await give_balance(app, "eco_gatherer1", 2000)
    s = await agents["eco_gatherer1"].status()
    if s["housing"]["homeless"]:
        await agents["eco_gatherer1"].call("rent_housing", {"zone": "outskirts"})

    mine_reg = await agents["eco_gatherer1"].call(
        "register_business",
        {"name": "Test Mine", "type": "mine", "zone": "industrial"},
    )
    mine_id = mine_reg["business_id"]

    prod_config = await agents["eco_gatherer1"].call(
        "configure_production",
        {"business_id": mine_id, "product": "copper_ore"},
    )
    assert prod_config["selected_recipe"] == "mine_copper"

    clock.advance(120)
    mine_work = await agents["eco_gatherer1"].call("work", {"business_id": mine_id})
    assert mine_work["produced"]["good"] == "copper_ore"
    assert mine_work["recipe_slug"] == "mine_copper"
    print(f"  Mine: recipe={mine_work['recipe_slug']}, produced {mine_work['produced']['good']}")

    # ------------------------------------------------------------------
    # Multi-business work routing with business_id
    # ------------------------------------------------------------------
    print_section("Work routing with business_id")

    await give_balance(app, "eco_lumberjack", 2000)
    lj_farm_reg = await agents["eco_lumberjack"].call(
        "register_business",
        {"name": "LJ Farm", "type": "farm", "zone": "outskirts"},
    )
    lj_farm_id = lj_farm_reg["business_id"]
    await agents["eco_lumberjack"].call("configure_production", {"business_id": lj_farm_id, "product": "wheat"})

    clock.advance(120)
    lj_work_lumber = await agents["eco_lumberjack"].call("work", {"business_id": lumber_id})
    assert lj_work_lumber["produced"]["good"] == "lumber"
    assert lj_work_lumber["business_id"] == lumber_id

    clock.advance(120)
    lj_work_farm = await agents["eco_lumberjack"].call("work", {"business_id": lj_farm_id})
    assert lj_work_farm["produced"]["good"] == "wheat"
    assert lj_work_farm["business_id"] == lj_farm_id
    print("  Multi-business routing: lumber mill → lumber, farm → wheat")

    # ------------------------------------------------------------------
    # Employee auto-deposit inputs on work()
    # ------------------------------------------------------------------
    print_section("Employee auto-deposit")

    await give_inventory(app, "eco_worker1", "wheat", 20)
    async with app.state.session_factory() as session:
        mill_wheat = (
            await session.execute(
                select(InventoryItem).where(
                    InventoryItem.owner_type == "business",
                    InventoryItem.owner_id == _uuid.UUID(mill_id),
                    InventoryItem.good_slug == "wheat",
                )
            )
        ).scalar_one_or_none()
        if mill_wheat:
            mill_wheat.quantity = 0
        await session.commit()

    clock.advance(120)
    auto_dep = await agents["eco_worker1"].call("work", {})
    assert auto_dep["produced"]["good"] == "flour", "Worker should produce flour via auto-deposit"
    assert auto_dep["employed"] is True

    w1_status = await agents["eco_worker1"].status()
    w1_wheat = next((i for i in w1_status["inventory"] if i["good_slug"] == "wheat"), None)
    w1_wheat_qty = w1_wheat["quantity"] if w1_wheat else 0
    assert w1_wheat_qty < 20, f"Worker wheat should be consumed via auto-deposit, still has {w1_wheat_qty}"
    print(f"  worker1 wheat: 20 → {w1_wheat_qty} (auto-deposited)")

    # ------------------------------------------------------------------
    # NPC business auto-restock on employee work()
    # ------------------------------------------------------------------
    print_section("NPC business auto-restock")

    from backend.models.business import Business as BizModel
    from backend.models.business import JobPosting as JPModel
    from backend.models.recipe import Recipe as RecipeModel

    async with app.state.session_factory() as session:
        npc_biz_result = await session.execute(
            select(BizModel).where(
                BizModel.is_npc == True,  # noqa: E712
                BizModel.closed_at.is_(None),
                BizModel.default_recipe_slug.isnot(None),
            )
        )
        npc_businesses = list(npc_biz_result.scalars().all())

    npc_biz_for_test = None
    npc_recipe = None
    for nb in npc_businesses:
        async with app.state.session_factory() as session:
            r = (
                await session.execute(select(RecipeModel).where(RecipeModel.slug == nb.default_recipe_slug))
            ).scalar_one_or_none()
            if r and r.inputs_json:
                npc_biz_for_test = nb
                npc_recipe = r
                break

    if npc_biz_for_test is not None:
        async with app.state.session_factory() as session:
            jp = (
                await session.execute(
                    select(JPModel).where(
                        JPModel.business_id == npc_biz_for_test.id,
                        JPModel.is_active == True,  # noqa: E712
                    )
                )
            ).scalar_one_or_none()

        if jp is not None:
            trader = agents["eco_trader"]
            trader_status = await trader.status()
            if not (trader_status.get("employment") or {}).get("employed"):
                # Empty the NPC business inputs to force restock
                async with app.state.session_factory() as session:
                    for inp in npc_recipe.inputs_json:
                        inp_slug = inp.get("good_slug") or inp.get("good")
                        inv = (
                            await session.execute(
                                select(InventoryItem).where(
                                    InventoryItem.owner_type == "business",
                                    InventoryItem.owner_id == npc_biz_for_test.id,
                                    InventoryItem.good_slug == inp_slug,
                                )
                            )
                        ).scalar_one_or_none()
                        if inv:
                            inv.quantity = 0
                    await session.commit()

                clock.advance(120)
                await trader.call("apply_job", {"job_id": str(jp.id)})
                clock.advance(120)
                npc_work = await trader.call("work", {})
                assert npc_work["produced"]["good"] == npc_recipe.output_good
                print(f"  eco_trader produced {npc_work['produced']['good']} at NPC business (auto-restocked)")
                await trader.call("manage_employees", {"action": "quit_job"})
            else:
                print("  eco_trader already employed, skipping NPC restock test")
        else:
            print("  No NPC job posting found, skipping NPC restock test")
    else:
        print("  No NPC business with input recipe found, skipping NPC restock test")

    # ------------------------------------------------------------------
    # Employee overflow: goods to personal inventory when business full
    # ------------------------------------------------------------------
    print_section("Employee overflow (storage full)")

    # Create a tiny-storage business for overflow testing
    overflow_owner = await TestAgent.signup(client, "overflow_boss")
    overflow_worker = await TestAgent.signup(client, "overflow_worker")
    await give_balance(app, "overflow_boss", 5000)
    await give_balance(app, "overflow_worker", 500)
    await overflow_owner.call("rent_housing", {"zone": "outskirts"})
    await overflow_worker.call("rent_housing", {"zone": "outskirts"})

    overflow_biz = await overflow_owner.call(
        "register_business",
        {"name": "Tiny Farm", "type": "farm", "zone": "outskirts"},
    )
    overflow_biz_id = overflow_biz["business_id"]
    await overflow_owner.call(
        "configure_production",
        {"business_id": overflow_biz_id, "product": "wheat"},
    )

    # Post a job and hire the worker
    overflow_job = await overflow_owner.call(
        "manage_employees",
        {
            "business_id": overflow_biz_id,
            "action": "post_job",
            "title": "Wheat Farmer",
            "wage": 10.0,
            "product": "wheat",
            "max_workers": 1,
        },
    )
    await overflow_worker.call("apply_job", {"job_id": overflow_job["job_id"]})

    # Fill business storage to near-capacity via direct DB insert
    async with app.state.session_factory() as session:
        biz_uuid = _uuid.UUID(overflow_biz_id)
        # wheat has storage_size=1, business_capacity=500
        # Fill with 498 wheat so producing 5 more would exceed capacity
        session.add(
            InventoryItem(
                owner_type="business",
                owner_id=biz_uuid,
                good_slug="wheat",
                quantity=498,
            )
        )
        await session.commit()

    # Worker works — business is nearly full, output (5 wheat) overflows to employee
    clock.advance(120)
    worker_bal_before = await get_balance(app, "overflow_worker")
    overflow_result = await overflow_worker.call("work", {})

    assert overflow_result["produced"]["good"] == "wheat"
    assert overflow_result["produced"]["overflow_to_employee"] is True, (
        "Expected overflow_to_employee=True when business storage is full"
    )
    assert overflow_result["employed"] is True
    assert overflow_result["wage_earned"] == 10.0

    # Verify worker got paid
    worker_bal_after = await get_balance(app, "overflow_worker")
    assert float(worker_bal_after - worker_bal_before) == 10.0

    # Verify goods are in worker's personal inventory, not business
    worker_wheat = await get_inventory_qty(app, "overflow_worker", "wheat")
    assert worker_wheat >= 5, f"Worker should have ≥5 wheat from overflow, got {worker_wheat}"

    # Verify business inventory didn't change (still 498)
    async with app.state.session_factory() as session:
        biz_inv = (
            await session.execute(
                select(InventoryItem).where(
                    InventoryItem.owner_type == "business",
                    InventoryItem.owner_id == _uuid.UUID(overflow_biz_id),
                    InventoryItem.good_slug == "wheat",
                )
            )
        ).scalar_one_or_none()
        assert biz_inv.quantity == 498, f"Business wheat should stay at 498, got {biz_inv.quantity}"
    print(f"  Overflow: worker got {worker_wheat} wheat + wage 10.0, business stayed at 498")

    # Clean up: quit job
    await overflow_worker.call("manage_employees", {"action": "quit_job"})

    # ------------------------------------------------------------------
    # Closed business transfer test
    # ------------------------------------------------------------------
    print_section("Closed business transfer")

    transfer_owner = await TestAgent.signup(client, "transfer_owner")
    await give_balance(app, "transfer_owner", 1000)
    await transfer_owner.call("rent_housing", {"zone": "outskirts"})
    closed_biz = await transfer_owner.call(
        "register_business",
        {"name": "Close Me Biz", "type": "mill", "zone": "industrial"},
    )
    closed_biz_id = closed_biz["business_id"]
    clock.advance(31)
    await transfer_owner.call("manage_employees", {"business_id": closed_biz_id, "action": "close_business"})
    _, err = await transfer_owner.try_call(
        "business_inventory",
        {"action": "withdraw", "business_id": closed_biz_id, "good": "wheat", "quantity": 1},
    )
    assert err == "INVALID_PARAMS"
    print("  Cannot transfer on closed business")

    # ------------------------------------------------------------------
    # Run 2 days + verify storefront sales
    # ------------------------------------------------------------------
    print_section("2-day simulation + storefront sales")

    await run_tick(hours=48)

    all_sale_events = []
    for name in ["eco_baker", "eco_miller", "eco_lumberjack"]:
        events_result = await agents[name].call("events", {})
        sales = [e for e in events_result.get("events", []) if e["type"] == "storefront_sale"]
        if sales:
            ev = sales[0]
            assert "business_name" in ev["detail"]
            assert "good_slug" in ev["detail"]
            assert "revenue" in ev["detail"]
            assert "message" in ev["detail"]
        all_sale_events.extend(sales)
    assert len(all_sale_events) > 0, "At least one business owner should have storefront_sale events after 2 days"
    print(f"  {len(all_sale_events)} storefront_sale events across owners")

    print("\n  Business & Employment COMPLETE")
