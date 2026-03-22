"""
Full Playthrough Simulation — Real Test of the Agent Economy

Runs 9 agents with distinct economic strategies through 14 simulated days.
Each agent makes decisions every tick cycle, exercising the full tool suite:
gathering, marketplace, business registration, production, employment,
banking, direct trading, and voting.

STRATEGIES:
  1. "The Gatherer"       — Gathers berries/herbs nonstop, sells on marketplace. Outskirts.
  2. "The Industrialist"  — Gathers iron ore, registers smithy, smelts ingots, sells. Industrial.
  3. "The Baker"          — Gathers wheat, registers mill+bakery, produces bread. Suburbs.
  4. "The Trader"         — Buys low on marketplace, sells high. Exploits price gaps.
  5. "The Banker"         — Uses loans to bootstrap a business, leverages banking.
  6. "The Idle"           — Control group. Does nothing. Should go bankrupt.
  7. "The Employee"       — Gets hired at an NPC business, earns wages.
  8. "The Diversifier"    — Gathers multiple resources, spreads risk.
  9. "The Downtown Mogul" — High-risk: expensive zone, high-value production.

GOAL: Find bugs, fairness issues, broken mechanics, and economic imbalances.
"""

from __future__ import annotations

import traceback
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select

from backend.models.agent import Agent
from backend.models.inventory import InventoryItem
from backend.models.transaction import Transaction
from tests.conftest import give_balance, get_balance, give_inventory, force_agent_age
from tests.helpers import TestAgent, ToolCallError


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

class SimLog:
    """Collects structured findings during the simulation."""

    def __init__(self):
        self.findings: list[dict] = []
        self.errors: list[dict] = []
        self.metrics: list[dict] = []
        self.timeline: list[str] = []

    def finding(self, category: str, severity: str, description: str, detail: str = ""):
        entry = {"category": category, "severity": severity, "description": description, "detail": detail}
        self.findings.append(entry)
        print(f"  [FINDING][{severity}] {category}: {description}")
        if detail:
            print(f"    Detail: {detail}")

    def error(self, agent: str, action: str, error_code: str, message: str):
        entry = {"agent": agent, "action": action, "code": error_code, "message": message}
        self.errors.append(entry)

    def event(self, msg: str):
        self.timeline.append(msg)
        print(f"  >> {msg}")

    def metric(self, label: str, data: dict):
        self.metrics.append({"label": label, **data})

    def print_summary(self):
        print(f"\n{'='*70}")
        print("SIMULATION FINDINGS SUMMARY")
        print(f"{'='*70}")

        by_severity: dict[str, list] = {}
        for f in self.findings:
            by_severity.setdefault(f["severity"], []).append(f)

        for sev in ["BUG", "CRITICAL", "FAIRNESS", "DESIGN", "INFO"]:
            items = by_severity.get(sev, [])
            if items:
                print(f"\n--- {sev} ({len(items)}) ---")
                for f in items:
                    print(f"  [{f['category']}] {f['description']}")
                    if f["detail"]:
                        print(f"    {f['detail']}")

        print(f"\n--- Error Codes Encountered ({len(self.errors)}) ---")
        code_counts: dict[str, int] = {}
        for e in self.errors:
            code_counts[e["code"]] = code_counts.get(e["code"], 0) + 1
        for code, count in sorted(code_counts.items(), key=lambda x: -x[1]):
            print(f"  {code}: {count}x")
        print(f"{'='*70}")


def print_agent_report(label: str, agents_data: list[dict]):
    """Print a detailed report of all agents."""
    print(f"\n{'='*70}")
    print(f"[{label}]")
    print(f"{'='*70}")

    balances = [a["balance"] for a in agents_data if a]
    housed = sum(1 for a in agents_data if a and not a["housing"]["homeless"])
    bankrupt = sum(1 for a in agents_data if a and a.get("bankruptcy_count", 0) > 0)

    print(f"  Agents: {len(agents_data)} | Housed: {housed} | Bankrupt: {bankrupt}")
    if balances:
        print(f"  Balances: min={min(balances):.2f}  max={max(balances):.2f}  "
              f"sum={sum(balances):.2f}  avg={sum(balances)/len(balances):.2f}")

    for a in agents_data:
        if not a:
            continue
        inv_items = a.get("inventory", [])
        inv_total = sum(i["quantity"] for i in inv_items)
        storage = a.get("storage", {})
        housing = "homeless" if a["housing"]["homeless"] else a["housing"].get("zone_slug", "housed")

        print(f"  {a['name']:22s} | bal={a['balance']:>9.2f} | {housing:10s} | "
              f"inv={inv_total:3d}/{storage.get('capacity', '?')} | "
              f"bankruptcies={a.get('bankruptcy_count', 0)}")
        if inv_items:
            inv_str = ", ".join(f"{i['good_slug']}={i['quantity']}" for i in inv_items[:8])
            print(f"    inventory: {inv_str}")
    print()


# ---------------------------------------------------------------------------
# Agent strategy functions
# ---------------------------------------------------------------------------

async def strategy_gatherer(agent: TestAgent, clock, day: int, log: SimLog) -> None:
    """Pure gatherer: gather berries and herbs, sell on marketplace."""
    resources = ["berries", "herbs", "cotton", "wheat"]

    for resource in resources:
        clock.advance(6)  # global cooldown
        result, err = await agent.try_call("gather", {"resource": resource})
        if err:
            log.error(agent.name, f"gather_{resource}", err, "")
        else:
            log.event(f"{agent.name} gathered {resource}")

    # Sell everything on marketplace every few days
    if day % 2 == 0:
        status = await agent.status()
        for item in status.get("inventory", []):
            if item["quantity"] >= 3:
                sell_qty = item["quantity"] - 1  # keep 1
                result, err = await agent.try_call("marketplace_order", {
                    "action": "sell",
                    "product": item["good_slug"],
                    "quantity": sell_qty,
                    "price": 3.0,
                })
                if err:
                    log.error(agent.name, f"sell_{item['good_slug']}", err, "")
                else:
                    log.event(f"{agent.name} listed {sell_qty}x {item['good_slug']} @ 3.0")


async def strategy_industrialist(agent: TestAgent, clock, day: int, log: SimLog, app) -> None:
    """Gathers iron ore, registers smithy, smelts ingots, sells on marketplace."""
    # Gather iron ore and wood
    for resource in ["iron_ore", "wood"]:
        clock.advance(61)
        result, err = await agent.try_call("gather", {"resource": resource})
        if err:
            log.error(agent.name, f"gather_{resource}", err, "")
        else:
            log.event(f"{agent.name} gathered {resource}")

    # Register business on day 2
    if day == 2:
        status = await agent.status()
        if status["balance"] >= 200 and not status["housing"]["homeless"]:
            result, err = await agent.try_call("register_business", {
                "name": "Iron Works",
                "type": "smithy",
                "zone": "industrial",
            })
            if err:
                log.error(agent.name, "register_business", err, "")
                log.finding("BUSINESS", "DESIGN",
                           f"Industrialist couldn't register business: {err}",
                           f"Balance={status['balance']}, housing={status['housing']}")
            else:
                log.event(f"{agent.name} registered Iron Works smithy")
                biz_id = result.get("business_id")
                if biz_id:
                    # configure_production uses "product" not "recipe_slug"
                    res2, err2 = await agent.try_call("configure_production", {
                        "business_id": biz_id,
                        "product": "iron_ingots",
                    })
                    if err2:
                        log.error(agent.name, "configure_production", err2, "")
                    else:
                        log.event(f"{agent.name} configured production: iron_ingots")
        else:
            if status["balance"] < 200:
                log.finding("FAIRNESS", "DESIGN",
                           f"Industrialist can't afford business on day {day}",
                           f"Balance={status['balance']:.2f}, need 200")

    # Try to work (produce)
    clock.advance(61)
    result, err = await agent.try_call("work", {})
    if err:
        log.error(agent.name, "work", err, "")
    else:
        log.event(f"{agent.name} produced: {result.get('produced', result.get('output_good', '?'))}")

    # Sell iron_ingots if we have any
    status = await agent.status()
    for item in status.get("inventory", []):
        if item["good_slug"] == "iron_ingots" and item["quantity"] >= 2:
            result, err = await agent.try_call("marketplace_order", {
                "action": "sell",
                "product": "iron_ingots",
                "quantity": item["quantity"],
                "price": 12.0,
            })
            if err:
                log.error(agent.name, "sell_ingots", err, "")
            else:
                log.event(f"{agent.name} listed {item['quantity']}x iron_ingots @ 12")


async def strategy_baker(agent: TestAgent, clock, day: int, log: SimLog, app) -> None:
    """Vertical integration: wheat->flour->bread."""
    # Gather wheat and berries
    for resource in ["wheat", "berries", "wheat"]:
        clock.advance(61)
        result, err = await agent.try_call("gather", {"resource": resource})
        if err:
            log.error(agent.name, f"gather_{resource}", err, "")

    # Register mill on day 2
    if day == 2:
        status = await agent.status()
        if status["balance"] >= 200 and not status["housing"]["homeless"]:
            result, err = await agent.try_call("register_business", {
                "name": "Flour Mill",
                "type": "mill",
                "zone": "industrial",
            })
            if err:
                log.error(agent.name, "register_mill", err, "")
            else:
                log.event(f"{agent.name} registered Flour Mill")
                biz_id = result.get("business_id")
                if biz_id:
                    await agent.try_call("configure_production", {
                        "business_id": biz_id,
                        "product": "flour",
                    })

    # Register bakery on day 5
    if day == 5:
        status = await agent.status()
        if status["balance"] >= 200 and not status["housing"]["homeless"]:
            result, err = await agent.try_call("register_business", {
                "name": "Daily Bread Bakery",
                "type": "bakery",
                "zone": "suburbs",
            })
            if err:
                log.error(agent.name, "register_bakery", err, "")
                log.finding("BUSINESS", "DESIGN",
                           f"Baker couldn't register bakery on day {day}",
                           f"Balance={status['balance']:.2f}")
            else:
                log.event(f"{agent.name} registered Daily Bread Bakery")
                biz_id = result.get("business_id")
                if biz_id:
                    await agent.try_call("configure_production", {
                        "business_id": biz_id,
                        "product": "bread",
                    })

    # Work
    clock.advance(61)
    result, err = await agent.try_call("work", {})
    if err:
        log.error(agent.name, "work", err, "")
    else:
        log.event(f"{agent.name} produced: {result.get('produced', result.get('output_good', '?'))}")

    # Sell bread or flour
    status = await agent.status()
    for item in status.get("inventory", []):
        if item["good_slug"] in ("bread", "flour") and item["quantity"] >= 2:
            price = 22.0 if item["good_slug"] == "bread" else 10.0
            result, err = await agent.try_call("marketplace_order", {
                "action": "sell",
                "product": item["good_slug"],
                "quantity": item["quantity"] - 1,
                "price": price,
            })
            if err:
                log.error(agent.name, f"sell_{item['good_slug']}", err, "")


async def strategy_trader(agent: TestAgent, clock, day: int, log: SimLog) -> None:
    """Marketplace arbitrage: buy cheap raw goods, sell at markup."""
    cheap_goods = ["berries", "wood", "herbs"]

    for good in cheap_goods:
        result, err = await agent.try_call("marketplace_browse", {"product": good})
        if err:
            log.error(agent.name, f"browse_{good}", err, "")
            continue

        if result and isinstance(result, dict):
            # Check for sell orders (asks) we can buy cheaply
            asks = result.get("asks", result.get("sell_orders", []))
            if isinstance(asks, list):
                for order in asks[:2]:
                    if isinstance(order, dict):
                        order_price = order.get("price", 999)
                        order_qty = order.get("quantity_available", order.get("quantity", 0))
                        if order_price <= 4.0 and order_qty > 0:
                            buy_qty = min(order_qty, 10)
                            res, err2 = await agent.try_call("marketplace_order", {
                                "action": "buy",
                                "product": good,
                                "quantity": buy_qty,
                                "price": order_price + 0.5,
                            })
                            if err2:
                                log.error(agent.name, f"buy_{good}", err2, "")
                            else:
                                log.event(f"{agent.name} bought {buy_qty}x {good} @ {order_price + 0.5}")

    # Resell at markup
    if day % 2 == 1:
        status = await agent.status()
        for item in status.get("inventory", []):
            if item["quantity"] >= 2:
                result, err = await agent.try_call("marketplace_order", {
                    "action": "sell",
                    "product": item["good_slug"],
                    "quantity": item["quantity"],
                    "price": 5.0,
                })
                if err:
                    log.error(agent.name, f"resell_{item['good_slug']}", err, "")

    # Gather for income floor
    clock.advance(6)
    await agent.try_call("gather", {"resource": "berries"})


async def strategy_banker(agent: TestAgent, clock, day: int, log: SimLog, app) -> None:
    """Uses banking system: deposits, loans, leveraged business."""
    for resource in ["berries", "herbs"]:
        clock.advance(6)
        await agent.try_call("gather", {"resource": resource})

    # Deposit on day 1
    if day == 1:
        status = await agent.status()
        if status["balance"] > 50:
            deposit_amt = status["balance"] - 20
            result, err = await agent.try_call("bank", {
                "action": "deposit",
                "amount": deposit_amt,
            })
            if err:
                log.error(agent.name, "deposit", err, "")
            else:
                log.event(f"{agent.name} deposited {deposit_amt:.2f}")

    # Take loan on day 3
    if day == 3:
        status = await agent.status()
        result, err = await agent.try_call("bank", {
            "action": "take_loan",
            "amount": 500,
        })
        if err:
            log.error(agent.name, "take_loan", err, "")
            log.finding("BANKING", "DESIGN",
                       f"Loan denied on day {day}: {err}",
                       f"Balance={status['balance']:.2f}")
        else:
            log.event(f"{agent.name} borrowed 500")

    # Register business on day 4
    if day == 4:
        status = await agent.status()
        if status["balance"] >= 200 and not status["housing"]["homeless"]:
            result, err = await agent.try_call("register_business", {
                "name": "Herb Apothecary",
                "type": "apothecary",
                "zone": "suburbs",
            })
            if err:
                log.error(agent.name, "register_business", err, "")
            else:
                log.event(f"{agent.name} registered Herb Apothecary")
                biz_id = result.get("business_id")
                if biz_id:
                    await agent.try_call("configure_production", {
                        "business_id": biz_id,
                        "product": "herbs_dried",
                    })

    # Work
    clock.advance(6)
    result, err = await agent.try_call("work", {})
    if err:
        log.error(agent.name, "work", err, "")
    else:
        if result:
            log.event(f"{agent.name} produced: {result.get('produced', result.get('output_good', '?'))}")

    # Check bank balance
    if day % 3 == 0:
        result, err = await agent.try_call("bank", {"action": "view_balance"})
        if not err and result:
            log.event(f"{agent.name} bank: {result.get('account_balance', '?')}")


async def strategy_employee(agent: TestAgent, clock, day: int, log: SimLog) -> None:
    """Tries to get employed at an NPC business and earn wages."""
    # Gather as fallback income
    clock.advance(6)
    await agent.try_call("gather", {"resource": "berries"})
    clock.advance(6)
    await agent.try_call("gather", {"resource": "wheat"})

    # Look for jobs on day 1+
    if day >= 1:
        # List all jobs
        result, err = await agent.try_call("list_jobs", {})
        if err:
            log.error(agent.name, "list_jobs", err, "")
        elif result:
            jobs = result.get("jobs", result.get("postings", []))
            if isinstance(jobs, list) and jobs:
                # Apply to first available job
                job = jobs[0]
                job_id = job.get("id", job.get("job_id", job.get("posting_id")))
                if job_id:
                    res2, err2 = await agent.try_call("apply_job", {"job_id": str(job_id)})
                    if err2:
                        log.error(agent.name, "apply_job", err2, "")
                    else:
                        log.event(f"{agent.name} applied for job: {job.get('title', '?')}")

    # Try to work (will use employment if accepted)
    clock.advance(61)
    result, err = await agent.try_call("work", {})
    if err:
        log.error(agent.name, "work", err, "")
    else:
        log.event(f"{agent.name} worked: earned {result.get('wage', result.get('wage_earned', '?'))}")

    # Sell gathered goods
    if day % 3 == 0:
        status = await agent.status()
        for item in status.get("inventory", []):
            if item["quantity"] >= 3:
                await agent.try_call("marketplace_order", {
                    "action": "sell",
                    "product": item["good_slug"],
                    "quantity": item["quantity"] - 1,
                    "price": 3.0,
                })


async def strategy_diversifier(agent: TestAgent, clock, day: int, log: SimLog) -> None:
    """Gathers many different resources, sells variety on marketplace."""
    # Rotate through all gatherable resources
    all_resources = ["berries", "herbs", "cotton", "wheat", "wood", "stone", "clay", "iron_ore", "sand"]
    day_resources = [all_resources[i % len(all_resources)] for i in range(day * 3, day * 3 + 3)]

    for resource in day_resources:
        clock.advance(61)
        result, err = await agent.try_call("gather", {"resource": resource})
        if err:
            log.error(agent.name, f"gather_{resource}", err, "")
        else:
            log.event(f"{agent.name} gathered {resource}")

    # Sell everything with decent stock
    if day % 2 == 0:
        status = await agent.status()
        for item in status.get("inventory", []):
            if item["quantity"] >= 4:
                await agent.try_call("marketplace_order", {
                    "action": "sell",
                    "product": item["good_slug"],
                    "quantity": item["quantity"] - 1,
                    "price": 4.0,
                })
                log.event(f"{agent.name} listed {item['quantity']-1}x {item['good_slug']} @ 4.0")


async def strategy_mogul(agent: TestAgent, clock, day: int, log: SimLog, app) -> None:
    """High-risk downtown business: expensive rent, high-value production."""
    # Gather high-value resources
    for resource in ["iron_ore", "herbs", "copper_ore"]:
        clock.advance(61)
        result, err = await agent.try_call("gather", {"resource": resource})
        if err:
            log.error(agent.name, f"gather_{resource}", err, "")

    # Register a jeweler downtown on day 3
    if day == 3:
        status = await agent.status()
        if status["balance"] >= 200 and not status["housing"]["homeless"]:
            result, err = await agent.try_call("register_business", {
                "name": "Fine Jewels",
                "type": "jeweler",
                "zone": "downtown",
            })
            if err:
                log.error(agent.name, "register_business", err, "")
                log.finding("BUSINESS", "DESIGN",
                           f"Mogul couldn't register downtown business: {err}",
                           f"Balance={status['balance']:.2f}")
            else:
                log.event(f"{agent.name} registered Fine Jewels in downtown")
                biz_id = result.get("business_id")
                if biz_id:
                    await agent.try_call("configure_production", {
                        "business_id": biz_id,
                        "product": "jewelry",
                    })

    # Work
    clock.advance(61)
    result, err = await agent.try_call("work", {})
    if err:
        log.error(agent.name, "work", err, "")
    else:
        log.event(f"{agent.name} produced: {result.get('produced', result.get('output_good', '?'))}")

    # Sell high-value goods
    status = await agent.status()
    for item in status.get("inventory", []):
        if item["good_slug"] == "jewelry" and item["quantity"] >= 1:
            await agent.try_call("marketplace_order", {
                "action": "sell",
                "product": "jewelry",
                "quantity": item["quantity"],
                "price": 80.0,
            })
            log.event(f"{agent.name} listed jewelry @ 80.0")


# ---------------------------------------------------------------------------
# Direct trade helper
# ---------------------------------------------------------------------------

async def _try_direct_trade(agent_a: TestAgent, agent_b: TestAgent, clock, log: SimLog):
    """Test the direct trade / escrow system between two agents."""
    status_a = await agent_a.status()
    a_inv = status_a.get("inventory", [])

    if not a_inv or a_inv[0]["quantity"] < 1:
        log.finding("TRADE", "INFO", f"{agent_a.name} has no inventory to trade")
        return

    offer_item = a_inv[0]
    result, err = await agent_a.try_call("trade", {
        "action": "propose",
        "target_agent": agent_b.name,
        "offer_items": [{"good_slug": offer_item["good_slug"], "quantity": 1}],
        "request_items": [],
        "offer_money": 0,
        "request_money": 10,
    })
    if err:
        log.error(agent_a.name, "trade_propose", err, f"offering {offer_item['good_slug']}")
        log.finding("TRADE", "BUG" if err == "UNKNOWN" else "INFO",
                    f"Trade proposal failed: {err}",
                    f"{agent_a.name} -> {agent_b.name}, offering {offer_item['good_slug']}")
    else:
        trade_id = result.get("trade_id")
        log.event(f"{agent_a.name} proposed trade to {agent_b.name}: "
                 f"1x {offer_item['good_slug']} for 10 currency (id={trade_id})")

        # Agent B accepts the trade
        if trade_id:
            res2, err2 = await agent_b.try_call("trade", {
                "action": "respond",
                "trade_id": trade_id,
                "accept": True,
            })
            if err2:
                log.error(agent_b.name, "trade_respond", err2, "")
                log.finding("TRADE", "BUG" if err2 == "UNKNOWN" else "INFO",
                           f"Trade accept failed: {err2}")
            else:
                log.event(f"{agent_b.name} accepted trade — deal complete!")


# ---------------------------------------------------------------------------
# Main simulation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_playthrough(client, app, clock, run_tick, db, redis_client):
    """
    14-day full playthrough with 9 agents using different strategies.
    """
    log = SimLog()

    print(f"\n\n{'#'*70}")
    print("# FULL PLAYTHROUGH SIMULATION — 9 AGENTS, 14 DAYS")
    print(f"# Start: {clock.now().isoformat()}")
    print(f"{'#'*70}")

    # -----------------------------------------------------------------------
    # Phase 1: Setup — sign up all agents
    # -----------------------------------------------------------------------
    print("\n=== PHASE 1: AGENT SIGNUP ===")

    agent_configs = [
        ("gatherer", "Pure resource extraction + marketplace sales"),
        ("industrialist", "Iron smelting business in industrial zone"),
        ("baker", "Wheat->flour->bread vertical chain"),
        ("trader", "Marketplace arbitrage"),
        ("banker", "Leveraged business via loans"),
        ("idle", "Control group — does nothing"),
        ("employee", "Wage worker at NPC businesses"),
        ("diversifier", "Gathers everything, sells variety"),
        ("mogul", "Downtown jeweler, high-risk high-reward"),
    ]

    agents: dict[str, TestAgent] = {}
    for name, desc in agent_configs:
        agent = await TestAgent.signup(client, name)
        agents[name] = agent
        print(f"  Signed up: {name} — {desc}")

    # -----------------------------------------------------------------------
    # Phase 2: Seed starting capital
    # -----------------------------------------------------------------------
    print("\n=== PHASE 2: SEED STARTING CAPITAL ===")

    # Seeded amounts calibrated for each strategy's needs.
    # Costs per day: outskirts = (5+8)*24 = 312/day, suburbs = (5+25)*24 = 720/day
    # downtown = (5+50)*24 = 1320/day. Agents also earn from gathering (cash bonus).
    # Gatherer in outskirts: ~4 gathers/round * 4 rounds * ~1.0 cash each = ~16/day
    # So gathering barely dents costs. Seed enough for 3-5 days to test strategies.
    seed_amounts = {
        "gatherer": 1500,
        "industrialist": 2000,
        "baker": 3000,
        "trader": 1500,
        "banker": 1500,
        "idle": 500,
        "employee": 1500,
        "diversifier": 1500,
        "mogul": 5000,
    }

    for name, amount in seed_amounts.items():
        await give_balance(app, name, amount)
        log.event(f"Seeded {name} with {amount}")

    # -----------------------------------------------------------------------
    # Phase 3: Housing
    # -----------------------------------------------------------------------
    print("\n=== PHASE 3: HOUSING ===")

    housing_plan = {
        "gatherer": "outskirts",
        "industrialist": "industrial",
        "baker": "suburbs",
        "trader": "outskirts",
        "banker": "outskirts",
        "employee": "outskirts",
        "diversifier": "outskirts",
        "mogul": "downtown",
        # idle stays homeless
    }

    for name, zone in housing_plan.items():
        agent = agents[name]
        result, err = await agent.try_call("rent_housing", {"zone": zone})
        if err:
            log.error(name, "rent_housing", err, f"zone={zone}")
            log.finding("HOUSING", "BUG", f"{name} couldn't rent {zone}: {err}")
        else:
            log.event(f"{name} rented {zone} (rent={result['rent_cost_per_hour']}/hr)")

    print("\n  Post-housing balances:")
    for name, agent in agents.items():
        status = await agent.status()
        print(f"    {name}: {status['balance']:.2f}")

    # -----------------------------------------------------------------------
    # Phase 4: Initial gathering burst
    # -----------------------------------------------------------------------
    print("\n=== PHASE 4: INITIAL GATHERING BURST ===")

    gather_targets = {
        "gatherer": ["berries", "herbs", "cotton", "wheat", "berries"],
        "industrialist": ["iron_ore", "iron_ore", "wood", "iron_ore"],
        "baker": ["wheat", "wheat", "wheat", "berries"],
        "banker": ["herbs", "herbs", "berries"],
        "employee": ["berries", "wheat"],
        "diversifier": ["berries", "wood", "stone", "herbs", "clay"],
        "mogul": ["iron_ore", "copper_ore", "herbs"],
    }

    for name, resources in gather_targets.items():
        agent = agents[name]
        gathered = 0
        for resource in resources:
            clock.advance(61)
            result, err = await agent.try_call("gather", {"resource": resource})
            if not err:
                gathered += 1
        log.event(f"{name} gathered {gathered}/{len(resources)} in initial burst")

    # -----------------------------------------------------------------------
    # Phase 5: Run 14 days of simulation
    # Each day: 4 action rounds (every 6h), then a 6h tick
    # This gives agents 4 chances to earn per day, with costs applied hourly
    # -----------------------------------------------------------------------
    print("\n=== PHASE 5: 14-DAY SIMULATION ===")

    bankrupt_agents: set[str] = set()
    daily_snapshots: list[dict] = []
    ROUNDS_PER_DAY = 4  # one action round every 6 hours

    for day in range(14):
        print(f"\n--- Day {day + 1} ---")

        for round_num in range(ROUNDS_PER_DAY):
            # Each agent executes their strategy
            for name, agent in agents.items():
                if name in bankrupt_agents or name == "idle":
                    continue

                try:
                    if name == "gatherer":
                        await strategy_gatherer(agent, clock, day, log)
                    elif name == "industrialist":
                        await strategy_industrialist(agent, clock, day, log, app)
                    elif name == "baker":
                        await strategy_baker(agent, clock, day, log, app)
                    elif name == "trader":
                        await strategy_trader(agent, clock, day, log)
                    elif name == "banker":
                        await strategy_banker(agent, clock, day, log, app)
                    elif name == "employee":
                        await strategy_employee(agent, clock, day, log)
                    elif name == "diversifier":
                        await strategy_diversifier(agent, clock, day, log)
                    elif name == "mogul":
                        await strategy_mogul(agent, clock, day, log, app)
                except ToolCallError as e:
                    log.error(name, e.tool_name, e.code, e.message)
                except Exception as e:
                    log.finding("RUNTIME", "BUG",
                               f"Exception in {name}'s strategy: {type(e).__name__}",
                               f"{traceback.format_exc()[-200:]}")

            # Direct trade test on day 4, round 2
            if day == 4 and round_num == 1 and "gatherer" not in bankrupt_agents and "trader" not in bankrupt_agents:
                await _try_direct_trade(agents["gatherer"], agents["trader"], clock, log)

            # Run tick — advance 6h (costs: 6h * 5/hr = 30 survival + 6h rent)
            tick_result = await run_tick(hours=6)

            # Check for bankruptcies
            if tick_result:
                slow = tick_result.get("slow_tick", {})
                if isinstance(slow, dict):
                    bk = slow.get("bankruptcy", {})
                    if isinstance(bk, dict) and bk.get("count", 0) > 0:
                        bk_names = bk.get("bankrupted", [])
                        for bn in bk_names:
                            bankrupt_agents.add(bn)
                        log.event(f"Day {day+1} R{round_num+1} BANKRUPTCY: {bk_names}")

        # Daily snapshot
        snapshot = {"day": day + 1}
        for name, agent in agents.items():
            try:
                s = await agent.status()
                snapshot[name] = {
                    "balance": s["balance"],
                    "homeless": s["housing"]["homeless"],
                    "inv_count": sum(i["quantity"] for i in s.get("inventory", [])),
                    "bankruptcy_count": s.get("bankruptcy_count", 0),
                }
            except Exception:
                snapshot[name] = {"balance": 0, "homeless": True, "inv_count": 0, "bankruptcy_count": -1}
        daily_snapshots.append(snapshot)

        # Daily summary
        print(f"  Day {day+1} end:")
        for name in agents:
            d = snapshot[name]
            status_str = "BANKRUPT" if d["bankruptcy_count"] > 0 else ("homeless" if d["homeless"] else "housed")
            print(f"    {name:15s}: bal={d['balance']:>9.2f}  inv={d['inv_count']:3d}  {status_str}")

    # -----------------------------------------------------------------------
    # Phase 6: Voting
    # -----------------------------------------------------------------------
    print("\n=== PHASE 6: VOTING TEST ===")

    for name in agents:
        await force_agent_age(app, name, 1_300_000)

    vote_map = {
        "gatherer": "free_market",
        "industrialist": "free_market",
        "baker": "social_democracy",
        "trader": "libertarian",
        "banker": "social_democracy",
        "employee": "social_democracy",
        "diversifier": "free_market",
        "mogul": "libertarian",
    }

    for name, template in vote_map.items():
        if name in bankrupt_agents:
            continue
        result, err = await agents[name].try_call("vote", {"government_type": template})
        if err:
            log.error(name, "vote", err, f"template={template}")
            log.finding("VOTING", "BUG" if err == "UNKNOWN" else "INFO",
                       f"{name} couldn't vote for {template}: {err}")
        else:
            log.event(f"{name} voted for {template}")

    await run_tick(days=7)
    log.event("Weekly tick run — election tallied")

    # Check result
    for name, agent in agents.items():
        if name not in bankrupt_agents:
            try:
                econ, err = await agent.try_call("get_economy", {"section": "government"})
                if not err and econ:
                    current = econ.get("current_template", econ.get("template", {}))
                    if isinstance(current, dict):
                        log.event(f"Government: {current.get('slug', current.get('name', '?'))}")
                    else:
                        log.event(f"Government: {current}")
                    break
            except Exception:
                pass

    # -----------------------------------------------------------------------
    # Phase 7: Banking analysis
    # -----------------------------------------------------------------------
    print("\n=== PHASE 7: BANKING ANALYSIS ===")

    if "banker" not in bankrupt_agents:
        banker = agents["banker"]
        result, err = await banker.try_call("bank", {"action": "view_balance"})
        if not err and result:
            log.event(f"Banker's bank state: balance={result.get('account_balance', '?')}, "
                     f"loans={result.get('active_loans', result.get('loans', '?'))}")

        # Try withdrawal
        result, err = await banker.try_call("bank", {"action": "withdraw", "amount": 10})
        if err:
            log.error("banker", "withdraw", err, "")
        else:
            log.event(f"Banker withdrew 10: wallet={result.get('wallet_balance', '?')}")
    else:
        log.finding("BANKING", "DESIGN",
                   "Banker went bankrupt before banking could be fully tested",
                   "Starting capital or loan terms may need adjustment")

    # -----------------------------------------------------------------------
    # Phase 8: Marketplace analysis
    # -----------------------------------------------------------------------
    print("\n=== PHASE 8: MARKETPLACE ANALYSIS ===")

    test_goods = ["berries", "herbs", "wheat", "iron_ore", "iron_ingots", "flour", "bread"]
    for good in test_goods:
        for name, agent in agents.items():
            if name not in bankrupt_agents:
                result, err = await agent.try_call("marketplace_browse", {"product": good})
                if not err and result:
                    # Parse the response format
                    asks = result.get("asks", [])
                    bids = result.get("bids", [])
                    recent = result.get("recent_trades", [])
                    if asks or bids or recent:
                        print(f"    {good}: {len(asks)} asks, {len(bids)} bids, {len(recent)} recent trades")
                    else:
                        print(f"    {good}: empty")
                break

    # -----------------------------------------------------------------------
    # Phase 9: Final analysis
    # -----------------------------------------------------------------------
    print("\n=== PHASE 9: FINAL ANALYSIS ===")

    final_statuses = {}
    for name, agent in agents.items():
        try:
            final_statuses[name] = await agent.status()
        except Exception as e:
            log.finding("RUNTIME", "BUG", f"Can't get final status for {name}: {e}")
            final_statuses[name] = None

    print_agent_report("FINAL STATE (Day 14)", [s for s in final_statuses.values() if s])

    # Assertion 1: Idle agent should be bankrupt or deeply in debt
    idle_final = final_statuses.get("idle")
    if idle_final:
        if idle_final.get("bankruptcy_count", 0) > 0:
            log.finding("ECONOMY", "INFO", "Idle agent went bankrupt as expected")
        elif idle_final["balance"] < -100:
            log.finding("ECONOMY", "INFO",
                       f"Idle agent in severe debt ({idle_final['balance']:.2f})")
        elif idle_final["balance"] > 0:
            log.finding("ECONOMY", "BUG",
                       f"Idle agent still has positive balance ({idle_final['balance']:.2f}) after 14 days!",
                       "Survival costs may not be deducting correctly")

    # Assertion 2: Active agents should have survived (at least the gatherer)
    for name in ["gatherer", "diversifier"]:
        status = final_statuses.get(name)
        if status and status.get("bankruptcy_count", 0) > 0:
            log.finding("FAIRNESS", "CRITICAL",
                       f"{name} went bankrupt despite active strategy!",
                       f"Balance={status['balance']:.2f}")

    # Assertion 3: No negative inventory
    inv_result = await db.execute(
        select(InventoryItem).where(InventoryItem.quantity < 0)
    )
    neg_inv = list(inv_result.scalars().all())
    if neg_inv:
        for item in neg_inv:
            log.finding("INTEGRITY", "BUG",
                       f"Negative inventory: owner={item.owner_id} good={item.good_slug} qty={item.quantity}")
    else:
        log.finding("INTEGRITY", "INFO", "No negative inventory found")

    # Assertion 4: Money supply check (player agents only, excluding NPCs)
    player_names = [name for name, _ in agent_configs]
    balance_result = await db.execute(
        select(func.sum(Agent.balance)).where(Agent.name.in_(player_names))
    )
    player_balance = float(balance_result.scalar() or 0)
    initial_seed = sum(seed_amounts.values())

    # Also check all agents including NPCs + central bank
    all_balance_result = await db.execute(select(func.sum(Agent.balance)))
    total_all = float(all_balance_result.scalar() or 0)

    from backend.models.banking import CentralBank
    bank_result = await db.execute(select(CentralBank).where(CentralBank.id == 1))
    bank = bank_result.scalar_one_or_none()
    bank_reserves = float(bank.reserves) if bank else 0

    log.finding("ECONOMY", "INFO",
               f"Player balance: {player_balance:.2f} (seeded {initial_seed}), "
               f"All agents: {total_all:.2f}, Bank reserves: {bank_reserves:.2f}",
               f"Player diff: {player_balance - initial_seed:.2f}")

    # Assertion 5: Transaction records
    for txn_type in ["food", "rent", "storefront", "marketplace_sale"]:
        count_result = await db.execute(
            select(func.count()).select_from(Transaction).where(Transaction.type == txn_type)
        )
        count = count_result.scalar()
        log.metric(txn_type, {"count": count})
        print(f"  Txn '{txn_type}': {count}")

    # Assertion 6: Wealth inequality
    active_balances = []
    for name in ["gatherer", "industrialist", "baker", "trader", "banker", "employee", "diversifier", "mogul"]:
        s = final_statuses.get(name)
        if s:
            active_balances.append((name, s["balance"]))

    if len(active_balances) >= 2:
        active_balances.sort(key=lambda x: -x[1])
        max_bal = active_balances[0][1]
        min_bal = active_balances[-1][1]
        avg = sum(b for _, b in active_balances) / len(active_balances)
        log.finding("FAIRNESS", "INFO",
                   f"Wealth spread: max={max_bal:.2f} ({active_balances[0][0]}), "
                   f"min={min_bal:.2f} ({active_balances[-1][0]}), avg={avg:.2f}")

    # Strategy ranking
    print("\n  Strategy effectiveness ranking:")
    ranking = []
    for name in ["gatherer", "industrialist", "baker", "trader", "banker", "employee", "diversifier", "mogul"]:
        s = final_statuses.get(name)
        if s:
            net_worth = s["balance"]
            for item in s.get("inventory", []):
                net_worth += item["quantity"] * 2
            ranking.append((name, net_worth, s["balance"], s.get("bankruptcy_count", 0)))

    ranking.sort(key=lambda x: -x[1])
    for rank, (name, nw, bal, bk) in enumerate(ranking, 1):
        bk_str = f" BANKRUPT({bk})" if bk > 0 else ""
        print(f"    #{rank} {name:15s}: net_worth~{nw:.2f}  (cash={bal:.2f}){bk_str}")

    # Pending trades check
    from backend.models.marketplace import Trade
    stuck_trades = await db.execute(select(Trade).where(Trade.status == "pending"))
    stuck = list(stuck_trades.scalars().all())
    if stuck:
        log.finding("TRADE", "DESIGN",
                   f"{len(stuck)} pending trades at end of simulation")

    # Print findings
    log.print_summary()

    # Daily trajectories
    print(f"\n{'='*70}")
    print("DAILY BALANCE TRAJECTORIES")
    print(f"{'='*70}")
    header = f"{'Day':>4s}"
    for name, _ in agent_configs:
        header += f"  {name:>12s}"
    print(header)

    for snap in daily_snapshots:
        line = f"{snap['day']:4d}"
        for name, _ in agent_configs:
            d = snap.get(name, {})
            bal = d.get("balance", 0)
            marker = " B" if d.get("bankruptcy_count", 0) > 0 else ""
            line += f"  {bal:>10.2f}{marker}"
        print(line)

    print(f"\n{'='*70}")
    print("SIMULATION COMPLETE")
    print(f"{'='*70}")

    # Hard assertions
    assert len(neg_inv) == 0, "Negative inventory found"
    food_count = await db.execute(
        select(func.count()).select_from(Transaction).where(Transaction.type == "food")
    )
    assert food_count.scalar() > 0, "No food transactions — tick system broken"

    print("\nAll hard assertions passed.")
