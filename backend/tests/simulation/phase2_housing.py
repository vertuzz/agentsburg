"""Phase 2: Housing & Survival (Days 1-3) — rent, survival costs, tick deductions."""

from __future__ import annotations

from sqlalchemy import func, select

from backend.models.transaction import Transaction
from tests.conftest import get_balance, give_balance
from tests.helpers import TestAgent
from tests.simulation.helpers import AGENT_NAMES, print_phase, print_section


async def run_phase_2(agents: dict[str, TestAgent], client, app, clock, run_tick, redis_client):
    """Rent housing, run ticks, verify survival and rent costs."""
    print_phase(2, "HOUSING & SURVIVAL")

    # --- 2a: Give agents working capital ---
    print_section("Seeding agent balances")
    for name in AGENT_NAMES:
        if name == "eco_homeless":
            await give_balance(app, name, 15)  # stays at starting balance
        else:
            await give_balance(app, name, 500)

    # --- 2b: Rent housing ---
    print_section("Renting housing")

    # Outskirts: gatherers and workers
    for name in ["eco_gatherer1", "eco_gatherer2", "eco_worker1", "eco_worker2"]:
        result = await agents[name].call("rent_housing", {"zone": "outskirts"})
        assert result["zone_slug"] == "outskirts"
        assert result["rent_cost_per_hour"] == 5.0
        print(f"  {name}: outskirts (5/hr)")

    # Industrial: mill owner, lumber jack
    for name in ["eco_miller", "eco_lumberjack"]:
        result = await agents[name].call("rent_housing", {"zone": "industrial"})
        assert result["zone_slug"] == "industrial"
        assert result["rent_cost_per_hour"] == 15.0
        print(f"  {name}: industrial (15/hr)")

    # Suburbs: baker, trader, banker, politician
    for name in ["eco_baker", "eco_trader", "eco_banker", "eco_politician"]:
        result = await agents[name].call("rent_housing", {"zone": "suburbs"})
        assert result["zone_slug"] == "suburbs"
        assert result["rent_cost_per_hour"] == 25.0
        print(f"  {name}: suburbs (25/hr)")

    # Criminal gets outskirts
    result = await agents["eco_criminal"].call("rent_housing", {"zone": "outskirts"})
    assert result["zone_slug"] == "outskirts"
    print(f"  eco_criminal: outskirts (5/hr)")

    # eco_homeless stays homeless
    s = await agents["eco_homeless"].status()
    assert s["housing"]["homeless"] is True
    print(f"  eco_homeless: stays homeless")

    # --- 2c: Verify housed agents ---
    for name in ["eco_gatherer1", "eco_miller", "eco_baker"]:
        s = await agents[name].status()
        assert s["housing"]["homeless"] is False
        assert s["housing"]["zone_id"] is not None
    print("  Housing zone verification passed")

    # --- 2d: Run 2 days of ticks, verify survival + rent ---
    print_section("Running 2-day simulation (survival + rent)")
    balance_before = {}
    for name in AGENT_NAMES:
        balance_before[name] = await get_balance(app, name)

    await run_tick(hours=48)

    balance_after = {}
    for name in AGENT_NAMES:
        balance_after[name] = await get_balance(app, name)

    # Verify costs deducted
    for name in AGENT_NAMES:
        assert balance_after[name] < balance_before[name], \
            f"{name} balance should decrease: {balance_before[name]} -> {balance_after[name]}"

    # Homeless agent should only pay survival (2/hr * 48h = 96 max)
    homeless_spent = float(balance_before["eco_homeless"] - balance_after["eco_homeless"])
    print(f"  eco_homeless spent: {homeless_spent:.2f} (survival only)")

    # Suburbs agent pays survival + rent (2+25=27/hr * 48h = 1296 max)
    baker_spent = float(balance_before["eco_baker"] - balance_after["eco_baker"])
    print(f"  eco_baker spent: {baker_spent:.2f} (survival + suburbs rent)")
    assert baker_spent > homeless_spent, "Suburbs renter should spend more than homeless"

    # Verify transaction records
    async with app.state.session_factory() as session:
        food_count = (await session.execute(
            select(func.count()).select_from(Transaction).where(Transaction.type == "food")
        )).scalar()
        rent_count = (await session.execute(
            select(func.count()).select_from(Transaction).where(Transaction.type == "rent")
        )).scalar()
    assert food_count > 0, "No food transactions recorded"
    assert rent_count > 0, "No rent transactions recorded"
    print(f"  Transactions: {food_count} food, {rent_count} rent")

    # --- 2e: Events endpoint ---
    print_section("Events endpoint after tick")

    events_result = await agents["eco_baker"].call("events", {})
    assert "events" in events_result, "Events endpoint should return events list"
    assert isinstance(events_result["events"], list)
    event_types = {e["type"] for e in events_result["events"]}
    assert "food_charged" in event_types, f"Expected food_charged in events, got {event_types}"
    assert "rent_charged" in event_types, f"Expected rent_charged in events, got {event_types}"
    print(f"  Events endpoint: {len(events_result['events'])} events, types include food_charged + rent_charged")

    print("\n  Phase 2 COMPLETE")
