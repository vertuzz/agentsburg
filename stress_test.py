#!/usr/bin/env python3
"""
Adversarial stress test — 5 agents with different strategies play the economy.

Agents:
  1. Grinder     — Optimal gatherer, gathers highest-value resources, sells aggressively
  2. Exploiter   — Tries exploits: negative prices, duplicate signups, self-trade, overflow
  3. Capitalist  — Saves aggressively, opens business ASAP, hires NPCs
  4. Manipulator — Market manipulation: wash trading, cornering, price fixing
  5. Freeloader  — Minimal effort, tries to mooch off others via trades/loans
"""

import json
import random
import sys
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable

import httpx

BASE_URL = "http://localhost:8000"


# ─────────────────────────────────────────────
# REST API client
# ─────────────────────────────────────────────

TOOL_ROUTES = {
    "signup": ("POST", "/v1/signup"),
    "get_status": ("GET", "/v1/me"),
    "rent_housing": ("POST", "/v1/housing"),
    "gather": ("POST", "/v1/gather"),
    "register_business": ("POST", "/v1/businesses"),
    "configure_production": ("POST", "/v1/businesses/production"),
    "set_prices": ("POST", "/v1/businesses/prices"),
    "manage_employees": ("POST", "/v1/employees"),
    "list_jobs": ("GET", "/v1/jobs"),
    "apply_job": ("POST", "/v1/jobs/apply"),
    "work": ("POST", "/v1/work"),
    "marketplace_order": ("POST", "/v1/market/orders"),
    "marketplace_browse": ("GET", "/v1/market"),
    "trade": ("POST", "/v1/trades"),
    "bank": ("POST", "/v1/bank"),
    "vote": ("POST", "/v1/vote"),
    "get_economy": ("GET", "/v1/economy"),
    "messages": ("POST", "/v1/messages"),
}


def api_call(client: httpx.Client, tool: str, params: dict, token: str | None = None) -> dict | None:
    """Call a REST API endpoint. POST sends JSON body, GET sends query params."""
    route = TOOL_ROUTES.get(tool)
    if not route:
        return {"_error": "UNKNOWN_TOOL", "_message": f"No route for tool: {tool}"}

    method, path = route
    url = f"{BASE_URL}{path}"
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        if method == "GET":
            resp = client.get(url, params=params, headers=headers, timeout=15)
        else:
            resp = client.post(url, json=params, headers=headers, timeout=15)

        body = resp.json()

        if resp.status_code >= 400 or not body.get("ok"):
            error = body.get("error", {})
            code = error.get("code", "UNKNOWN") if isinstance(error, dict) else "UNKNOWN"
            message = error.get("message", str(error)) if isinstance(error, dict) else str(error)
            return {"_error": code, "_message": message}

        return body.get("data", body)
    except Exception as e:
        return {"_error": "NETWORK", "_message": str(e)}


def is_error(result):
    return result is None or (isinstance(result, dict) and "_error" in result)


def err_msg(result):
    if result is None:
        return "No response"
    return result.get("_message", result.get("_error", "Unknown"))


# ─────────────────────────────────────────────
# Tracking
# ─────────────────────────────────────────────

@dataclass
class AgentLog:
    name: str
    strategy: str
    token: str = ""
    balance_history: list = field(default_factory=list)
    events: list = field(default_factory=list)
    exploits_found: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    total_gathered: int = 0
    total_cash_from_gathering: float = 0.0
    total_marketplace_sells: int = 0
    total_marketplace_buys: int = 0
    businesses_registered: int = 0
    loans_taken: int = 0
    bankruptcies: int = 0

    def log(self, msg: str):
        self.events.append(msg)
        print(f"  [{self.name}] {msg}")

    def exploit(self, msg: str):
        self.exploits_found.append(msg)
        print(f"  [{self.name}] *** EXPLOIT: {msg} ***")

    def error(self, msg: str):
        self.errors.append(msg)


# ─────────────────────────────────────────────
# Setup
# ─────────────────────────────────────────────

def setup_agent(client: httpx.Client, name: str, strategy: str) -> AgentLog | None:
    agent = AgentLog(name=name, strategy=strategy)
    result = api_call(client, "signup", {"name": name})
    if is_error(result):
        print(f"  [{name}] Signup failed: {err_msg(result)}")
        return None
    agent.token = result["action_token"]
    agent.log(f"Signed up (balance={result.get('balance', '?')})")

    # Check starting balance
    status = api_call(client, "get_status", {}, agent.token)
    if not is_error(status):
        bal = status["balance"]
        agent.balance_history.append(bal)
        agent.log(f"Starting balance: {bal}, homeless: {status['housing']['homeless']}")

        # Rent outskirts
        if status["housing"]["homeless"] and bal >= 5:
            rent = api_call(client, "rent_housing", {"zone": "outskirts"}, agent.token)
            if not is_error(rent):
                agent.log(f"Rented outskirts (cost={rent.get('rent_cost_per_hour', '?')}/hr)")
            else:
                agent.log(f"Failed to rent: {err_msg(rent)}")
    return agent


# ─────────────────────────────────────────────
# Strategy 1: GRINDER
# Optimizes gathering income — picks highest cash/sec resources
# ─────────────────────────────────────────────

def play_grinder(client: httpx.Client, agent: AgentLog, status: dict, turn: int):
    # Try to gather ALL available resources each turn (maximize income)
    optimal_order = ["copper_ore", "iron_ore", "fish", "herbs", "wood", "cotton", "wheat", "berries", "stone", "clay", "sand"]

    gathered_count = 0
    for resource in optimal_order:
        result = api_call(client, "gather", {"resource": resource}, agent.token)
        if not is_error(result):
            agent.total_gathered += 1
            gathered_count += 1
            cash = result.get("cash_earned", 0)
            agent.total_cash_from_gathering += cash
            agent.log(f"Gathered {resource} (+{cash})")

    if gathered_count == 0:
        pass  # silent when on cooldown

    # Sell excess inventory every 2 turns at reference prices (NPC bank will buy)
    # Reference prices: berries=3, herbs=4, wood=4, iron_ore=6, copper_ore=7, etc.
    ref_prices = {"berries": 3, "herbs": 4, "wood": 4, "cotton": 4, "wheat": 4,
                  "fish": 5, "stone": 3, "clay": 3, "sand": 2, "iron_ore": 6, "copper_ore": 7}
    if turn % 2 == 0:
        for item in status.get("inventory", []):
            if item["quantity"] >= 2:
                sell_qty = item["quantity"] - 1
                price = ref_prices.get(item["good_slug"], 4.0)
                result = api_call(client, "marketplace_order", {
                    "action": "sell",
                    "product": item["good_slug"],
                    "quantity": sell_qty,
                    "price": price,
                }, agent.token)
                if not is_error(result):
                    agent.total_marketplace_sells += 1
                    agent.log(f"Listed {sell_qty}x {item['good_slug']} @ {price}")

    # Try to upgrade housing when rich
    if turn == 30 and status["balance"] > 200:
        result = api_call(client, "rent_housing", {"zone": "suburbs"}, agent.token)
        if not is_error(result):
            agent.log(f"Upgraded to suburbs!")

    # Deposit savings periodically
    if turn % 15 == 0 and status["balance"] > 50:
        deposit_amt = status["balance"] - 30
        result = api_call(client, "bank", {"action": "deposit", "amount": deposit_amt}, agent.token)
        if not is_error(result):
            agent.log(f"Deposited {deposit_amt:.2f} in bank")


# ─────────────────────────────────────────────
# Strategy 2: EXPLOITER
# Actively tries to break the game
# ─────────────────────────────────────────────

def play_exploiter(client: httpx.Client, agent: AgentLog, status: dict, turn: int):
    # Always gather for income
    result = api_call(client, "gather", {"resource": "berries"}, agent.token)
    if not is_error(result):
        agent.total_gathered += 1
        agent.total_cash_from_gathering += result.get("cash_earned", 0)

    # --- EXPLOIT ATTEMPTS ---

    if turn == 1:
        # Try duplicate signup
        result = api_call(client, "signup", {"name": agent.name})
        if not is_error(result) and "action_token" in result:
            agent.exploit("DUPLICATE SIGNUP ALLOWED — got new token!")
        else:
            agent.log("Duplicate signup correctly blocked")

    if turn == 2:
        # Try negative price sell
        result = api_call(client, "marketplace_order", {
            "action": "sell", "product": "berries", "quantity": 1, "price": -10.0,
        }, agent.token)
        if not is_error(result):
            agent.exploit("NEGATIVE PRICE SELL ACCEPTED!")
        else:
            agent.log("Negative price correctly rejected")

    if turn == 3:
        # Try zero price sell (should now be blocked)
        result = api_call(client, "marketplace_order", {
            "action": "sell", "product": "berries", "quantity": 1, "price": 0.0,
        }, agent.token)
        if not is_error(result):
            agent.exploit("ZERO PRICE SELL ACCEPTED — could be used for tax-free transfer!")
        else:
            agent.log("Zero price correctly rejected")

    if turn == 4:
        # Try selling items we don't have
        result = api_call(client, "marketplace_order", {
            "action": "sell", "product": "jewelry", "quantity": 100, "price": 1.0,
        }, agent.token)
        if not is_error(result):
            agent.exploit("SOLD ITEMS WE DON'T HAVE — phantom inventory!")
        else:
            agent.log("Can't sell items we don't own: correct")

    if turn == 5:
        # Try buying with no money (huge quantity)
        result = api_call(client, "marketplace_order", {
            "action": "buy", "product": "berries", "quantity": 999999, "price": 1000.0,
        }, agent.token)
        if not is_error(result):
            agent.exploit(f"HUGE BUY ORDER ACCEPTED — could drain market! Result: {result}")
        else:
            agent.log(f"Huge buy order rejected: {err_msg(result)}")

    if turn == 6:
        # Try self-trade
        result = api_call(client, "trade", {
            "action": "propose",
            "target_agent": agent.name,
            "offer_money": 100,
            "request_money": 0,
        }, agent.token)
        if not is_error(result):
            agent.exploit("SELF-TRADE ALLOWED — potential money duplication!")
        else:
            agent.log(f"Self-trade rejected: {err_msg(result)}")

    if turn == 7:
        # Try to take a loan with 0 net worth
        result = api_call(client, "bank", {"action": "take_loan", "amount": 100000}, agent.token)
        if not is_error(result):
            agent.exploit(f"MASSIVE LOAN APPROVED with low net worth! Amount: {result}")
        else:
            agent.log(f"Excessive loan rejected: {err_msg(result)}")

    if turn == 8:
        # Try to withdraw more than deposited
        result = api_call(client, "bank", {"action": "withdraw", "amount": 999999}, agent.token)
        if not is_error(result):
            agent.exploit("OVERDRAFT ALLOWED — withdrew more than deposited!")
        else:
            agent.log(f"Overdraft rejected: {err_msg(result)}")

    if turn == 9:
        # Try renting housing without enough money
        result = api_call(client, "rent_housing", {"zone": "downtown"}, agent.token)
        if not is_error(result):
            # Check if we actually got downgraded balance
            s = api_call(client, "get_status", {}, agent.token)
            if s and s.get("balance", 0) < 0:
                agent.exploit("RENTED DOWNTOWN WITH NEGATIVE BALANCE!")
            else:
                agent.log(f"Rented downtown (might be affordable)")
        else:
            agent.log(f"Downtown rent rejected: {err_msg(result)}")

    if turn == 10:
        # Try to register business while homeless (should fail)
        # First evict ourselves by trying to rent invalid zone
        result = api_call(client, "register_business", {
            "name": "Exploit Corp", "type": "bakery", "zone": "outskirts",
        }, agent.token)
        if not is_error(result):
            agent.log(f"Business registration: {result.get('business_id', '?')}")
        else:
            agent.log(f"Business reg: {err_msg(result)}")

    if turn == 11:
        # Try to gather a non-existent resource
        result = api_call(client, "gather", {"resource": "diamonds"}, agent.token)
        if not is_error(result):
            agent.exploit("GATHERED NON-EXISTENT RESOURCE 'diamonds'!")
        else:
            agent.log("Non-existent resource correctly rejected")

    if turn == 12:
        # Try to send message to non-existent agent
        result = api_call(client, "messages", {
            "action": "send", "to_agent": "nonexistent_agent_xyz", "text": "Hello",
        }, agent.token)
        if not is_error(result):
            agent.exploit("SENT MESSAGE TO NON-EXISTENT AGENT!")
        else:
            agent.log("Message to non-existent agent rejected")

    if turn == 13:
        # Try extremely long message
        grinder_name = getattr(play_exploiter, '_grinder_name', None) or "nobody"
        long_text = "A" * 5000
        result = api_call(client, "messages", {
            "action": "send", "to_agent": grinder_name, "text": long_text,
        }, agent.token)
        if not is_error(result):
            agent.exploit(f"5000-CHAR MESSAGE ACCEPTED (limit should be 1000)")
        else:
            agent.log("Long message rejected")

    if turn == 14:
        # Try calling tools without auth
        result = api_call(client, "get_status", {})  # no token
        if not is_error(result) and "balance" in result:
            agent.exploit("GET_STATUS WORKS WITHOUT AUTH!")
        else:
            agent.log("No-auth status correctly rejected")

    if turn == 15:
        # Try marketplace order cancel on non-existent order
        result = api_call(client, "marketplace_order", {
            "action": "cancel", "order_id": "00000000-0000-0000-0000-000000000000",
        }, agent.token)
        if not is_error(result):
            agent.exploit("CANCELLED NON-EXISTENT ORDER!")
        else:
            agent.log("Cancel non-existent order rejected")

    if turn == 16:
        # Rapid-fire gathering same resource (concurrency exploit)
        results = []
        for _ in range(5):
            r = api_call(client, "gather", {"resource": "sand"}, agent.token)
            results.append(r)
        success_count = sum(1 for r in results if not is_error(r))
        if success_count > 1:
            agent.exploit(f"RAPID-FIRE GATHER: {success_count}/5 succeeded — cooldown bypass!")
        else:
            agent.log(f"Rapid-fire gather: {success_count}/5 succeeded (expected ≤1)")

    if turn == 17:
        # Try vote without eligibility
        result = api_call(client, "vote", {"government_type": "free_market"}, agent.token)
        if not is_error(result):
            agent.exploit("NEW AGENT VOTED — age check bypassed!")
        else:
            agent.log(f"Vote rejected (expected — too young): {err_msg(result)}")

    if turn == 18:
        # Try to apply for non-existent job
        result = api_call(client, "apply_job", {
            "job_id": "00000000-0000-0000-0000-000000000000",
        }, agent.token)
        if not is_error(result):
            agent.exploit("APPLIED TO NON-EXISTENT JOB!")
        else:
            agent.log("Non-existent job application rejected")

    if turn == 19:
        # Try to trade offering more money than we have
        # Use a global lookup to find the actual Grinder agent name
        grinder_name = getattr(play_exploiter, '_grinder_name', None)
        if grinder_name:
            result = api_call(client, "trade", {
                "action": "propose",
                "target_agent": grinder_name,
                "offer_money": 999999,
                "request_items": [{"good_slug": "berries", "quantity": 1}],
            }, agent.token)
            if not is_error(result):
                agent.exploit(f"TRADE PROPOSED WITH 999999 CASH WE DON'T HAVE!")
            else:
                agent.log(f"Insufficient funds trade rejected: {err_msg(result)}")

    if turn == 20:
        # Try to work without employment
        result = api_call(client, "work", {}, agent.token)
        if not is_error(result):
            agent.exploit("WORK WITHOUT EMPLOYMENT SUCCEEDED!")
        else:
            agent.log(f"Work without job rejected: {err_msg(result)}")

    # After all exploits, keep gathering ALL resources for income
    if turn > 20:
        for resource in ["copper_ore", "iron_ore", "fish", "herbs", "wood", "wheat", "berries", "cotton", "sand"]:
            result = api_call(client, "gather", {"resource": resource}, agent.token)
            if not is_error(result):
                agent.total_gathered += 1
                agent.total_cash_from_gathering += result.get("cash_earned", 0)


# ─────────────────────────────────────────────
# Strategy 3: CAPITALIST
# Saves money, opens business, hires NPCs, scales
# ─────────────────────────────────────────────

def play_capitalist(client: httpx.Client, agent: AgentLog, status: dict, turn: int):
    businesses = status.get("businesses", [])

    # Phase 1: Gather aggressively for capital
    if not businesses:
        for resource in ["iron_ore", "copper_ore", "fish", "herbs", "wood", "wheat", "berries", "cotton"]:
            result = api_call(client, "gather", {"resource": resource}, agent.token)
            if not is_error(result):
                agent.total_gathered += 1
                agent.total_cash_from_gathering += result.get("cash_earned", 0)
                agent.log(f"Gathered {resource} (+{result.get('cash_earned', 0)})")

    # Phase 2: Register business when we have enough capital
    if not businesses and status["balance"] >= 250 and not status["housing"]["homeless"]:
        result = api_call(client, "register_business", {
            "name": f"{agent.name}'s Bakery",
            "type": "bakery",
            "zone": "suburbs",
        }, agent.token)
        if not is_error(result):
            biz_id = result.get("business_id")
            agent.businesses_registered += 1
            agent.log(f"REGISTERED BAKERY! id={biz_id}")

            # Configure production
            api_call(client, "configure_production", {
                "business_id": biz_id, "product": "bread",
            }, agent.token)

            # Set storefront price
            api_call(client, "set_prices", {
                "business_id": biz_id, "product": "bread", "price": 25.0,
            }, agent.token)
            agent.log("Configured bread production @ 25.0")

            # Post a job
            api_call(client, "manage_employees", {
                "action": "post_job",
                "business_id": biz_id,
                "title": "Baker",
                "wage": 20.0,
                "product": "bread",
                "max_workers": 3,
            }, agent.token)
            agent.log("Posted baker job @ 20/call")

            # Hire NPC worker
            result = api_call(client, "manage_employees", {
                "action": "hire_npc", "business_id": biz_id,
            }, agent.token)
            if not is_error(result):
                agent.log("Hired NPC worker!")
        else:
            agent.log(f"Business reg failed: {err_msg(result)}")

    # Phase 3: Work and manage business
    if businesses:
        biz_id = businesses[0].get("id")

        # Gather wheat for bread inputs
        for resource in ["wheat", "berries"]:
            result = api_call(client, "gather", {"resource": resource}, agent.token)
            if not is_error(result):
                agent.total_gathered += 1
                agent.total_cash_from_gathering += result.get("cash_earned", 0)
                break

        # Work
        result = api_call(client, "work", {}, agent.token)
        if not is_error(result):
            agent.log(f"Produced: {result.get('output_good', result.get('produced', '?'))}")
        elif result:
            agent.log(f"Work: {err_msg(result)}")

        # Sell bread on marketplace
        if turn % 5 == 0:
            for item in status.get("inventory", []):
                if item["good_slug"] == "bread" and item["quantity"] >= 1:
                    api_call(client, "marketplace_order", {
                        "action": "sell",
                        "product": "bread",
                        "quantity": item["quantity"],
                        "price": 22.0,
                    }, agent.token)
                    agent.total_marketplace_sells += 1
                    agent.log(f"Listed {item['quantity']}x bread @ 22.0")

        # Try to hire more NPCs when profitable
        if turn % 20 == 0 and status["balance"] > 500 and biz_id:
            result = api_call(client, "manage_employees", {
                "action": "hire_npc", "business_id": biz_id,
            }, agent.token)
            if not is_error(result):
                agent.log("Hired another NPC!")

    # Take a loan if we're close to business registration
    if not businesses and 100 <= status["balance"] < 250 and turn > 10:
        needed = 250 - status["balance"] + 50  # buffer
        result = api_call(client, "bank", {"action": "take_loan", "amount": needed}, agent.token)
        if not is_error(result):
            agent.loans_taken += 1
            agent.log(f"Took loan of {needed:.2f}")
        else:
            agent.log(f"Loan rejected: {err_msg(result)}")


# ─────────────────────────────────────────────
# Strategy 4: MANIPULATOR
# Market manipulation — wash trades, cornering, price fixing
# ─────────────────────────────────────────────

def play_manipulator(client: httpx.Client, agent: AgentLog, status: dict, turn: int):
    # Gather all available resources
    for resource in ["berries", "herbs", "sand", "wood", "cotton", "wheat", "stone", "clay"]:
        result = api_call(client, "gather", {"resource": resource}, agent.token)
        if not is_error(result):
            agent.total_gathered += 1
            agent.total_cash_from_gathering += result.get("cash_earned", 0)

    if turn == 3:
        # WASH TRADING: sell to self via marketplace to create fake volume
        # Place sell order at low price
        for item in status.get("inventory", []):
            if item["quantity"] >= 2:
                slug = item["good_slug"]
                # Sell at base_value
                sell_result = api_call(client, "marketplace_order", {
                    "action": "sell", "product": slug, "quantity": 1, "price": 2.0,
                }, agent.token)
                if not is_error(sell_result):
                    agent.log(f"Wash trade sell: 1x {slug} @ 2.0")
                    # Now buy our own order
                    buy_result = api_call(client, "marketplace_order", {
                        "action": "buy", "product": slug, "quantity": 1, "price": 2.0,
                    }, agent.token)
                    if not is_error(buy_result):
                        agent.exploit(f"WASH TRADE SUCCEEDED on {slug} — self-buy went through!")
                    else:
                        agent.log(f"Wash trade buy failed (might be good): {err_msg(buy_result)}")
                break

    if turn == 5:
        # PRICE MANIPULATION: list at extremely high price
        for item in status.get("inventory", []):
            if item["quantity"] >= 1:
                result = api_call(client, "marketplace_order", {
                    "action": "sell", "product": item["good_slug"],
                    "quantity": 1, "price": 10000.0,
                }, agent.token)
                if not is_error(result):
                    agent.log(f"Listed {item['good_slug']} @ 10000.0 — price manipulation test")
                    # Check if this affects price history
                break

    if turn == 8:
        # ORDER BOOK SPAM: place many small orders
        for i in range(25):  # Try to exceed max_orders_per_agent (20)
            result = api_call(client, "marketplace_order", {
                "action": "sell", "product": "berries", "quantity": 1, "price": 3.0 + i * 0.1,
            }, agent.token)
            if is_error(result):
                agent.log(f"Order spam stopped at order #{i+1}: {err_msg(result)}")
                break
        else:
            agent.exploit("PLACED 25 ORDERS — exceeded max_orders_per_agent limit!")

    if turn == 10:
        # CORNERING: buy all available supply of a good
        browse = api_call(client, "marketplace_browse", {"product": "herbs"}, agent.token)
        if not is_error(browse):
            asks = browse.get("asks", [])
            total_supply = sum(a.get("quantity_available", 0) for a in asks)
            agent.log(f"Herbs market: {len(asks)} asks, {total_supply} total supply")
            if asks and status["balance"] > 100:
                # Try to buy everything
                for ask in asks:
                    api_call(client, "marketplace_order", {
                        "action": "buy",
                        "product": "herbs",
                        "quantity": ask.get("quantity_available", 1),
                        "price": ask.get("price", 10) + 1,
                    }, agent.token)
                agent.log("Attempted market corner on herbs")

    if turn == 15:
        # CANCEL-AND-RELIST: cancel orders to manipulate price display
        browse = api_call(client, "marketplace_browse", {"product": "berries"}, agent.token)
        if not is_error(browse):
            my_asks = [a for a in browse.get("asks", []) if a.get("agent_name") == agent.name]
            for order in my_asks[:5]:
                oid = order.get("order_id")
                if oid:
                    api_call(client, "marketplace_order", {"action": "cancel", "order_id": oid}, agent.token)
            agent.log(f"Cancelled {min(len(my_asks), 5)} orders")

    # Sell inventory every few turns
    if turn % 4 == 0 and turn > 5:
        for item in status.get("inventory", []):
            if item["quantity"] >= 2:
                api_call(client, "marketplace_order", {
                    "action": "sell", "product": item["good_slug"],
                    "quantity": item["quantity"] - 1, "price": 4.0,
                }, agent.token)
                agent.total_marketplace_sells += 1


# ─────────────────────────────────────────────
# Strategy 5: FREELOADER
# Minimal work, tries to profit from others
# ─────────────────────────────────────────────

def play_freeloader(client: httpx.Client, agent: AgentLog, status: dict, turn: int):
    # Gather occasionally (every other turn) — minimal effort
    if turn % 2 == 0:
        result = api_call(client, "gather", {"resource": "berries"}, agent.token)
        if not is_error(result):
            agent.total_gathered += 1
            agent.total_cash_from_gathering += result.get("cash_earned", 0)

    if turn == 3:
        # Try to get a loan immediately (free money attempt)
        result = api_call(client, "bank", {"action": "take_loan", "amount": 500}, agent.token)
        if not is_error(result):
            agent.loans_taken += 1
            agent.log(f"Got 500 loan early! Free money exploit?")
            agent.exploit("LARGE EARLY LOAN — potential exploit if no repayment enforcement")
        else:
            agent.log(f"Early loan rejected: {err_msg(result)}")

    if turn == 5:
        # Look for jobs — try to find highest paying
        result = api_call(client, "list_jobs", {}, agent.token)
        if not is_error(result):
            jobs = result.get("jobs", [])
            agent.log(f"Found {len(jobs)} jobs available")
            if jobs:
                # Apply for best paying job
                best = max(jobs, key=lambda j: j.get("wage", 0))
                apply_result = api_call(client, "apply_job", {"job_id": best["id"]}, agent.token)
                if not is_error(apply_result):
                    agent.log(f"Got job: {best.get('title', '?')} @ {best.get('wage', '?')}/call")

    # If employed, work every turn
    if status.get("employment"):
        result = api_call(client, "work", {}, agent.token)
        if not is_error(result):
            agent.log(f"Worked: earned {result.get('wage_earned', '?')}")

    if turn == 10:
        # Try to propose unfair trade
        grinder_name = getattr(play_freeloader, '_grinder_name', None)
        if not grinder_name:
            return
        result = api_call(client, "trade", {
            "action": "propose",
            "target_agent": grinder_name,
            "offer_money": 1,
            "request_money": 100,
        }, agent.token)
        if not is_error(result):
            agent.log(f"Proposed unfair trade (1 for 100)")

    if turn == 15:
        # Browse marketplace for extremely cheap items
        for good in ["bread", "tools", "clothing"]:
            browse = api_call(client, "marketplace_browse", {"product": good}, agent.token)
            if not is_error(browse):
                asks = browse.get("asks", [])
                cheap = [a for a in asks if a.get("price", 999) < 5]
                if cheap:
                    agent.log(f"Found cheap {good}: {[a['price'] for a in cheap]}")

    if turn == 20:
        # Try to deposit 0.01 to farm interest
        result = api_call(client, "bank", {"action": "deposit", "amount": 0.01}, agent.token)
        if not is_error(result):
            agent.log("Deposited 0.01 — micro-deposit test")
            # Check if interest accrues
            bal = api_call(client, "bank", {"action": "view_balance"}, agent.token)
            if not is_error(bal):
                agent.log(f"Account balance: {bal.get('account_balance', '?')}")

    # Try to browse economy data
    if turn == 25:
        econ = api_call(client, "get_economy", {"section": "stats"}, agent.token)
        if not is_error(econ):
            agent.log(f"Economy stats: {json.dumps(econ, indent=2)[:500]}")

    if turn == 30:
        econ = api_call(client, "get_economy", {"section": "government"}, agent.token)
        if not is_error(econ):
            agent.log(f"Government: {econ.get('template', {}).get('name', '?')}")


# ─────────────────────────────────────────────
# Main simulation
# ─────────────────────────────────────────────

STRATEGIES: dict[str, Callable] = {
    "Grinder": play_grinder,
    "Exploiter": play_exploiter,
    "Capitalist": play_capitalist,
    "Manipulator": play_manipulator,
    "Freeloader": play_freeloader,
}

NUM_TURNS = 50
TICK_INTERVAL = 3.0  # seconds between turns — global gather cooldown is 2s


def run_simulation():
    client = httpx.Client()

    # Reset: check server health
    health = None
    try:
        health = client.get(f"{BASE_URL}/health", timeout=5).json()
    except Exception as e:
        print(f"Server not reachable: {e}")
        sys.exit(1)
    print(f"Server health: {health}")

    # Sign up all agents
    print("\n" + "=" * 60)
    print("PHASE 1: AGENT REGISTRATION")
    print("=" * 60)

    agents: dict[str, AgentLog] = {}
    ts = f"{int(time.time()) % 10000}"
    for name in STRATEGIES:
        unique_name = f"{name}_{ts}"
        agent = setup_agent(client, unique_name, name)
        if agent:
            agents[name] = agent
        time.sleep(0.5)

    # Build name lookup for inter-agent operations
    agent_names = {strategy: a.name for strategy, a in agents.items()}
    # Set name references for cross-agent exploit tests
    if "Grinder" in agent_names:
        play_exploiter._grinder_name = agent_names["Grinder"]
        play_freeloader._grinder_name = agent_names["Grinder"]

    if len(agents) < 5:
        print(f"\nWARNING: Only {len(agents)}/5 agents registered successfully")

    # Main simulation loop
    print("\n" + "=" * 60)
    print(f"PHASE 2: SIMULATION ({NUM_TURNS} turns)")
    print("=" * 60)

    for turn in range(NUM_TURNS):
        print(f"\n{'─' * 40} Turn {turn + 1}/{NUM_TURNS} {'─' * 40}")

        for strategy_name, agent in agents.items():
            strategy_fn = STRATEGIES[strategy_name]

            # Get status
            status = api_call(client, "get_status", {}, agent.token)
            if is_error(status):
                agent.error(f"Turn {turn}: Can't get status")
                continue

            agent.balance_history.append(status["balance"])
            agent.bankruptcies = status.get("bankruptcy_count", 0)

            try:
                strategy_fn(client, agent, status, turn)
            except Exception as e:
                agent.error(f"Turn {turn}: Strategy error: {e}")
                traceback.print_exc()

        # Trigger a fast tick every 5 turns to process NPC marketplace demand
        if turn % 5 == 4:
            try:
                tick_resp = client.post(f"{BASE_URL}/admin/tick", timeout=10)
                if tick_resp.status_code == 200:
                    tick_data = tick_resp.json()
                    processed = tick_data.get("processed", [])
                    for p in processed:
                        if p.get("type") == "npc_marketplace" and p.get("fills", 0) > 0:
                            print(f"  [TICK] NPC marketplace: {p['fills']} fills, spent {p['spent']:.2f}")
                        elif p.get("type") == "order_matching" and p.get("trades_executed", 0) > 0:
                            print(f"  [TICK] Order matching: {p['trades_executed']} trades")
            except Exception:
                pass

        time.sleep(TICK_INTERVAL)

    # Final status collection
    print("\n" + "=" * 60)
    print("PHASE 3: FINAL RESULTS")
    print("=" * 60)

    # Wait for rate limit to reset, then use fresh client for final queries
    print("\n  Waiting 30s for rate limit to reset...")
    client.close()
    time.sleep(30)
    client2 = httpx.Client(timeout=15.0)
    for name, agent in agents.items():
        status = api_call(client2, "get_status", {}, agent.token)
        if is_error(status):
            print(f"\n[{agent.name}] Could not get final status")
            continue

        bank = api_call(client2, "bank", {"action": "view_balance"}, agent.token)
        bank_bal = bank.get("account_balance", 0) if not is_error(bank) else 0

        inv = status.get("inventory", [])
        inv_total = sum(i["quantity"] for i in inv)
        inv_value = sum(i["quantity"] * i.get("base_value", 1) for i in inv)

        net_worth = status["balance"] + bank_bal + inv_value

        print(f"\n{'━' * 50}")
        print(f"  {agent.name} ({name})")
        print(f"{'━' * 50}")
        print(f"  Wallet:           {status['balance']:.2f}")
        print(f"  Bank deposit:     {bank_bal:.2f}")
        print(f"  Inventory:        {inv_total} items (est. value: {inv_value:.2f})")
        print(f"  Net worth:        {net_worth:.2f}")
        print(f"  Housing:          {'HOMELESS' if status['housing']['homeless'] else status['housing'].get('zone_slug', 'housed')}")
        print(f"  Businesses:       {len(status.get('businesses', []))}")
        print(f"  Bankruptcies:     {agent.bankruptcies}")
        print(f"  Total gathered:   {agent.total_gathered}")
        print(f"  Gather income:    {agent.total_cash_from_gathering:.2f}")
        print(f"  Marketplace sells:{agent.total_marketplace_sells}")
        print(f"  Loans taken:      {agent.loans_taken}")

        if agent.balance_history:
            peak = max(agent.balance_history)
            low = min(agent.balance_history)
            print(f"  Balance range:    {low:.2f} — {peak:.2f}")

        if agent.exploits_found:
            print(f"  EXPLOITS FOUND:   {len(agent.exploits_found)}")
            for e in agent.exploits_found:
                print(f"    ⚠  {e}")

        if agent.errors:
            print(f"  Errors:           {len(agent.errors)}")

    # Summary
    print("\n" + "=" * 60)
    print("EXPLOIT SUMMARY")
    print("=" * 60)
    total_exploits = []
    for name, agent in agents.items():
        total_exploits.extend(agent.exploits_found)
    if total_exploits:
        for i, e in enumerate(total_exploits, 1):
            print(f"  {i}. {e}")
    else:
        print("  No exploits found — all tested vectors were properly guarded!")

    print("\n" + "=" * 60)
    print("BALANCE ISSUES & OBSERVATIONS")
    print("=" * 60)

    # Check for balance issues
    for name, agent in agents.items():
        if agent.balance_history:
            final_bal = agent.balance_history[-1]
            if final_bal < 0:
                print(f"  ⚠  {agent.name}: Negative final balance ({final_bal:.2f})")
            if agent.bankruptcies > 0:
                print(f"  ⚠  {agent.name}: Went bankrupt {agent.bankruptcies} time(s)")
            if agent.total_gathered > 0:
                avg_income = agent.total_cash_from_gathering / agent.total_gathered
                print(f"  📊 {agent.name}: Avg gather income = {avg_income:.2f}/gather, "
                      f"total gathers = {agent.total_gathered}")

    # Economy-wide check
    print("\n  Economy-wide checks:")
    econ = api_call(client2, "get_economy", {"section": "stats"}, list(agents.values())[0].token)
    if not is_error(econ):
        print(f"    Economy stats: {json.dumps(econ, indent=2)[:1000]}")

    bank_info = api_call(client2, "get_economy", {"section": "market"}, list(agents.values())[0].token)
    if not is_error(bank_info):
        print(f"    Market info: {json.dumps(bank_info, indent=2)[:800]}")

    print("\n" + "=" * 60)
    print("SIMULATION COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    run_simulation()
