"""Phase 3: Businesses & Employment (Days 3-5) — register, produce, hire, work, commute."""

from __future__ import annotations

import uuid as _uuid

from sqlalchemy import select

from backend.models.inventory import InventoryItem
from tests.conftest import get_balance, give_balance, give_inventory
from tests.helpers import TestAgent
from tests.simulation.helpers import print_phase, print_section


async def run_phase_3(agents: dict[str, TestAgent], client, app, clock, run_tick, redis_client):
    """Register businesses, hire workers, produce goods, test inventory transfers.

    Returns (mill_id, bakery_id, lumber_id, farm_id, mill_job_id, bakery_job_id).
    """
    print_phase(3, "BUSINESS & EMPLOYMENT")

    # Top up balances for business owners and re-rent housing
    for name in ["eco_miller", "eco_baker", "eco_lumberjack",
                  "eco_worker1", "eco_worker2", "eco_trader",
                  "eco_banker", "eco_politician", "eco_criminal",
                  "eco_gatherer1", "eco_gatherer2"]:
        await give_balance(app, name, 2000)

    # Re-rent housing for agents who may have been evicted
    for name, zone in [
        ("eco_miller", "industrial"), ("eco_baker", "suburbs"),
        ("eco_lumberjack", "industrial"), ("eco_worker1", "outskirts"),
        ("eco_worker2", "outskirts"), ("eco_trader", "suburbs"),
        ("eco_banker", "suburbs"), ("eco_politician", "suburbs"),
        ("eco_criminal", "outskirts"), ("eco_gatherer1", "outskirts"),
        ("eco_gatherer2", "outskirts"),
    ]:
        s = await agents[name].status()
        if s["housing"]["homeless"]:
            await agents[name].call("rent_housing", {"zone": zone})

    # --- 3a: Register businesses ---
    print_section("Registering businesses")

    mill_reg = await agents["eco_miller"].call("register_business", {
        "name": "Grand Mill", "type": "mill", "zone": "industrial",
    })
    assert "business_id" in mill_reg
    mill_id = mill_reg["business_id"]
    print(f"  Registered: Grand Mill (mill, industrial) id={mill_id[:8]}...")

    bakery_reg = await agents["eco_baker"].call("register_business", {
        "name": "Sunrise Bakery", "type": "bakery", "zone": "suburbs",
    })
    bakery_id = bakery_reg["business_id"]
    print(f"  Registered: Sunrise Bakery (bakery, suburbs) id={bakery_id[:8]}...")

    lumber_reg = await agents["eco_lumberjack"].call("register_business", {
        "name": "Oak Lumber Co", "type": "lumber_mill", "zone": "industrial",
    })
    lumber_id = lumber_reg["business_id"]
    print(f"  Registered: Oak Lumber Co (lumber_mill, industrial) id={lumber_id[:8]}...")

    # Homeless cannot register
    _, err = await agents["eco_homeless"].try_call("register_business", {
        "name": "Fail Biz", "type": "mill", "zone": "industrial",
    })
    assert err is not None
    print(f"  Homeless agent cannot register business (error={err})")

    # --- 3b: Configure production ---
    print_section("Configuring production")

    config_mill = await agents["eco_miller"].call("configure_production", {
        "business_id": mill_id, "product": "flour",
    })
    assert config_mill["product_slug"] == "flour"
    assert config_mill["bonus_applies"] is True
    print(f"  Mill: flour (bonus={config_mill['bonus_applies']})")

    config_bakery = await agents["eco_baker"].call("configure_production", {
        "business_id": bakery_id, "product": "bread",
    })
    assert config_bakery["product_slug"] == "bread"
    assert config_bakery["bonus_applies"] is True
    print(f"  Bakery: bread (bonus={config_bakery['bonus_applies']})")

    config_lumber = await agents["eco_lumberjack"].call("configure_production", {
        "business_id": lumber_id, "product": "lumber",
    })
    assert config_lumber["product_slug"] == "lumber"
    print(f"  Lumber mill: lumber")

    # --- 3c: Set storefront prices ---
    print_section("Setting storefront prices")

    await agents["eco_miller"].call("set_prices", {
        "business_id": mill_id, "product": "flour", "price": 6.0,
    })
    await agents["eco_baker"].call("set_prices", {
        "business_id": bakery_id, "product": "bread", "price": 10.0,
    })
    await agents["eco_lumberjack"].call("set_prices", {
        "business_id": lumber_id, "product": "lumber", "price": 8.0,
    })
    print("  Prices set: flour=6, bread=10, lumber=8")

    # --- 3d: Post jobs ---
    print_section("Posting jobs")

    for name in ["eco_miller", "eco_baker", "eco_lumberjack"]:
        await give_balance(app, name, 2000)

    mill_job = await agents["eco_miller"].call("manage_employees", {
        "business_id": mill_id, "action": "post_job",
        "title": "Mill Hand", "wage": 5.0, "product": "flour", "max_workers": 2,
    })
    mill_job_id = mill_job["job_id"]

    bakery_job = await agents["eco_baker"].call("manage_employees", {
        "business_id": bakery_id, "action": "post_job",
        "title": "Baker", "wage": 7.0, "product": "bread", "max_workers": 2,
    })
    bakery_job_id = bakery_job["job_id"]

    print(f"  Mill job: wage=5, max=2")
    print(f"  Bakery job: wage=7, max=2")

    # --- 3e: Agents apply for jobs ---
    print_section("Applying for jobs")

    apply1 = await agents["eco_worker1"].call("apply_job", {"job_id": mill_job_id})
    assert "employment_id" in apply1
    print(f"  eco_worker1 hired at mill")

    apply2 = await agents["eco_worker2"].call("apply_job", {"job_id": bakery_job_id})
    assert "employment_id" in apply2
    print(f"  eco_worker2 hired at bakery")

    # Verify list_jobs
    jobs_list = await agents["eco_trader"].call("list_jobs", {})
    assert len(jobs_list["items"]) > 0
    print(f"  list_jobs shows {len(jobs_list['items'])} job postings")

    # --- 3f: Stock businesses via transfer endpoint ---
    print_section("Stocking businesses via POST /v1/businesses/inventory")

    await give_inventory(app, "eco_miller", "wheat", 60)
    await give_inventory(app, "eco_baker", "flour", 40)
    await give_inventory(app, "eco_baker", "berries", 20)
    await give_inventory(app, "eco_lumberjack", "wood", 60)

    deposit_mill = await agents["eco_miller"].call("business_inventory", {
        "action": "deposit", "business_id": mill_id, "good": "wheat", "quantity": 30,
    })
    assert deposit_mill["transferred"] == 30
    assert deposit_mill["good"] == "wheat"
    assert deposit_mill["action"] == "deposit"
    assert deposit_mill["business_storage"]["used"] > 0
    assert deposit_mill["agent_storage"]["free"] >= 0
    print(f"  Miller deposited 30 wheat (biz storage: {deposit_mill['business_storage']['used']}/{deposit_mill['business_storage']['capacity']})")

    # Transfer cooldown enforced
    _, err = await agents["eco_miller"].try_call("business_inventory", {
        "action": "deposit", "business_id": mill_id, "good": "wheat", "quantity": 10,
    })
    assert err == "COOLDOWN_ACTIVE"
    print("  Transfer cooldown enforced (30s)")

    clock.advance(31)
    await agents["eco_miller"].call("business_inventory", {
        "action": "deposit", "business_id": mill_id, "good": "wheat", "quantity": 30,
    })
    print("  Miller deposited remaining 30 wheat after cooldown")

    # Stock bakery
    clock.advance(31)
    await agents["eco_baker"].call("business_inventory", {
        "action": "deposit", "business_id": bakery_id, "good": "flour", "quantity": 40,
    })
    clock.advance(31)
    await agents["eco_baker"].call("business_inventory", {
        "action": "deposit", "business_id": bakery_id, "good": "berries", "quantity": 20,
    })
    print("  Baker deposited 40 flour + 20 berries")

    # Stock lumber mill
    clock.advance(31)
    await agents["eco_lumberjack"].call("business_inventory", {
        "action": "deposit", "business_id": lumber_id, "good": "wood", "quantity": 60,
    })
    print("  Lumberjack deposited 60 wood")

    # --- 3g: Workers work ---
    print_section("Workers producing goods")

    worker1_balance_before = await get_balance(app, "eco_worker1")
    work1 = await agents["eco_worker1"].call("work", {})
    assert work1["produced"]["good"] == "flour"
    assert work1["produced"]["quantity"] == 2
    assert work1["employed"] is True
    assert work1["wage_earned"] == 5.0
    worker1_balance_after = await get_balance(app, "eco_worker1")
    assert float(worker1_balance_after - worker1_balance_before) == 5.0
    print(f"  worker1: produced 2 flour, earned wage 5.0")

    _, err = await agents["eco_worker1"].try_call("work", {})
    assert err == "COOLDOWN_ACTIVE"
    print(f"  Work cooldown enforced")

    work2 = await agents["eco_worker2"].call("work", {})
    assert work2["produced"]["good"] == "bread"
    assert work2["produced"]["quantity"] == 3
    assert work2["wage_earned"] == 7.0
    print(f"  worker2: produced 3 bread, earned wage 7.0")

    # Self-employed owner works
    cooldown = work1["cooldown_seconds"]
    clock.advance(cooldown + 1)
    miller_work = await agents["eco_miller"].call("work", {})
    assert miller_work["produced"]["good"] == "flour"
    assert miller_work["employed"] is False
    print(f"  miller self-employed: produced flour (no wage)")

    clock.advance(cooldown + 1)
    lumber_work = await agents["eco_lumberjack"].call("work", {})
    assert lumber_work["produced"]["good"] == "lumber"
    print(f"  lumberjack: produced lumber")

    # Commute penalty check
    if work1.get("cooldown_breakdown", {}).get("commute_penalty"):
        print(f"  Commute penalty detected for worker1 (outskirts -> industrial)")
    else:
        print(f"  Worker1 cooldown={work1['cooldown_seconds']}s (may include commute)")

    # Verify business inventory via DB
    mill_uuid = _uuid.UUID(mill_id)
    async with app.state.session_factory() as session:
        flour_item = (await session.execute(
            select(InventoryItem).where(
                InventoryItem.owner_type == "business",
                InventoryItem.owner_id == mill_uuid,
                InventoryItem.good_slug == "flour",
            )
        )).scalar_one_or_none()
        flour_qty = flour_item.quantity if flour_item else 0
    assert flour_qty > 0, "Mill should have produced flour"
    print(f"  Mill business inventory: {flour_qty} flour")

    # --- 3h: Withdraw goods from business ---
    print_section("Withdrawing goods from business")

    clock.advance(31)
    withdraw_result = await agents["eco_miller"].call("business_inventory", {
        "action": "withdraw", "business_id": mill_id, "good": "flour", "quantity": 2,
    })
    assert withdraw_result["transferred"] == 2
    assert withdraw_result["action"] == "withdraw"
    miller_status = await agents["eco_miller"].status()
    miller_flour = [i for i in miller_status["inventory"] if i["good_slug"] == "flour"]
    assert len(miller_flour) > 0 and miller_flour[0]["quantity"] >= 2
    print(f"  Miller withdrew 2 flour to personal inventory")

    # --- 3i: Extraction recipes (zero-input production at farm) ---
    print_section("Testing extraction recipes at farm")

    await give_balance(app, "eco_gatherer2", 500)
    s = await agents["eco_gatherer2"].status()
    if s["housing"]["homeless"]:
        await agents["eco_gatherer2"].call("rent_housing", {"zone": "outskirts"})

    farm_reg = await agents["eco_gatherer2"].call("register_business", {
        "name": "Test Farm", "type": "farm", "zone": "industrial",
    })
    farm_id = farm_reg["business_id"]
    print(f"  Registered: Test Farm (farm, industrial) id={farm_id[:8]}...")

    config_farm = await agents["eco_gatherer2"].call("configure_production", {
        "business_id": farm_id, "product": "wheat",
    })
    assert config_farm["product_slug"] == "wheat"
    assert config_farm["bonus_applies"] is True
    print(f"  Farm configured for wheat (bonus={config_farm['bonus_applies']})")

    clock.advance(120)
    farm_work = await agents["eco_gatherer2"].call("work", {})
    assert farm_work["produced"]["good"] == "wheat"
    assert farm_work["produced"]["quantity"] == 3
    assert farm_work["employed"] is False
    print(f"  Extraction recipe: produced {farm_work['produced']['quantity']} wheat (no inputs!)")

    clock.advance(31)
    withdraw_wheat = await agents["eco_gatherer2"].call("business_inventory", {
        "action": "withdraw", "business_id": farm_id, "good": "wheat", "quantity": 2,
    })
    assert withdraw_wheat["transferred"] == 2
    print(f"  Withdrew 2 wheat from farm to personal inventory")

    # --- 3j: Inventory discard ---
    print_section("Testing inventory discard")

    await give_inventory(app, "eco_gatherer1", "stone", 10)
    g1_before = await agents["eco_gatherer1"].status()
    storage_before = g1_before["storage"]["used"]

    discard_result = await agents["eco_gatherer1"].call("inventory_discard", {
        "good": "stone", "quantity": 5,
    })
    assert discard_result["discarded"]["good"] == "stone"
    assert discard_result["discarded"]["quantity"] == 5
    assert discard_result["storage"]["used"] < storage_before
    print(f"  Discarded 5 stone, storage: {discard_result['storage']['used']}/{discard_result['storage']['capacity']}")

    _, err = await agents["eco_gatherer1"].try_call("inventory_discard", {
        "good": "stone", "quantity": 9999,
    })
    assert err == "INSUFFICIENT_INVENTORY"
    print(f"  Cannot discard more than owned (error={err})")

    # Run 2 days of ticks
    await run_tick(hours=48)
    print("  Ran 2 days of ticks")

    print("\n  Phase 3 COMPLETE")

    return mill_id, bakery_id, lumber_id, farm_id, mill_job_id, bakery_job_id
