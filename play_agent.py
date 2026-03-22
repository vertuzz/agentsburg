#!/usr/bin/env python3
"""
Autonomous Agent Player — plays the Agent Economy game via the REST API.

Each agent makes its own decisions based on its strategy and current state.
Runs in a loop: check status -> decide action -> execute -> wait -> repeat.

Usage:
    python play_agent.py <agent_name> <strategy>

Strategies: gatherer, industrialist, baker, trader, diversifier
"""

import argparse
import random
import sys
import time
import httpx

BASE_URL = "http://localhost:8000"
TICK_INTERVAL = 5  # seconds between actions

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
    """Call a REST API endpoint mapped from the given tool name."""
    method, path = TOOL_ROUTES[tool]
    url = f"{BASE_URL}{path}"

    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        if method == "GET":
            resp = client.get(url, params=params, headers=headers, timeout=10)
        else:
            resp = client.post(url, json=params, headers=headers, timeout=10)

        body = resp.json()

        if resp.status_code == 400:
            return {"_error": body.get("error_code", "UNKNOWN"), "_message": body.get("message", "")}

        if resp.status_code == 200:
            return body.get("data", body)

        # Other non-200 status codes
        return {"_error": f"HTTP_{resp.status_code}", "_message": str(body)}
    except Exception as e:
        return {"_error": "NETWORK", "_message": str(e)}


def signup(client: httpx.Client, name: str) -> str | None:
    """Sign up and return action_token."""
    result = api_call(client, "signup", {"name": name})
    if result and "action_token" in result:
        print(f"[{name}] Signed up! Token: {result['action_token'][:16]}...")
        return result["action_token"]
    elif result and result.get("_error"):
        print(f"[{name}] Signup failed: {result['_message']}")
        # Try to see if we can still use an existing agent
        return None
    return None


def get_status(client: httpx.Client, token: str, name: str) -> dict | None:
    result = api_call(client, "get_status", {}, token)
    if result and "_error" not in result:
        return result
    return None


def log(name: str, msg: str):
    print(f"[{name}] {msg}")


# ---------------------------------------------------------------------------
# Strategy: Gatherer
# ---------------------------------------------------------------------------
def play_gatherer(client: httpx.Client, token: str, name: str, status: dict, turn: int):
    """Gather resources, sell on marketplace."""
    resources = ["berries", "herbs", "cotton", "wheat", "wood", "sand"]
    resource = resources[turn % len(resources)]

    result = api_call(client, "gather", {"resource": resource}, token)
    if result and "_error" not in result:
        cash = result.get("cash_earned", 0)
        log(name, f"Gathered {resource} (+{cash} cash). Inventory: {result.get('new_inventory_quantity', '?')}")
    elif result and result["_error"] == "COOLDOWN_ACTIVE":
        # Try a different resource
        alt = resources[(turn + 3) % len(resources)]
        result = api_call(client, "gather", {"resource": alt}, token)
        if result and "_error" not in result:
            log(name, f"Gathered {alt} instead (+{result.get('cash_earned', 0)} cash)")

    # Sell excess inventory every 5 turns
    if turn % 5 == 0 and status:
        for item in status.get("inventory", []):
            if item["quantity"] >= 5:
                sell_qty = item["quantity"] - 2
                api_call(client, "marketplace_order", {
                    "action": "sell",
                    "product": item["good_slug"],
                    "quantity": sell_qty,
                    "price": 4.0,
                }, token)
                log(name, f"Listed {sell_qty}x {item['good_slug']} on marketplace @ 4.0")


# ---------------------------------------------------------------------------
# Strategy: Industrialist
# ---------------------------------------------------------------------------
def play_industrialist(client: httpx.Client, token: str, name: str, status: dict, turn: int):
    """Gather iron ore, build smithy, produce."""
    # Gather iron ore
    result = api_call(client, "gather", {"resource": "iron_ore"}, token)
    if result and "_error" not in result:
        log(name, f"Gathered iron_ore (+{result.get('cash_earned', 0)} cash)")
    else:
        # Fallback: gather wood
        result = api_call(client, "gather", {"resource": "wood"}, token)
        if result and "_error" not in result:
            log(name, f"Gathered wood (+{result.get('cash_earned', 0)} cash)")

    # Register business when we have enough
    businesses = status.get("businesses", [])
    if not businesses and status["balance"] >= 250 and not status["housing"]["homeless"]:
        result = api_call(client, "register_business", {
            "name": f"{name}'s Smithy",
            "type": "smithy",
            "zone": "industrial",
        }, token)
        if result and "_error" not in result:
            biz_id = result.get("business_id")
            log(name, f"Registered smithy! (id={biz_id})")
            if biz_id:
                api_call(client, "configure_production", {
                    "business_id": biz_id,
                    "product": "iron_ingots",
                }, token)
                log(name, "Configured production: iron_ingots")

    # Try to work
    if businesses:
        result = api_call(client, "work", {}, token)
        if result and "_error" not in result:
            log(name, f"Produced! Output: {result.get('output_good', result.get('produced', '?'))}")
        elif result and result["_error"] == "INSUFFICIENT_INVENTORY":
            log(name, "Need more iron_ore inputs. Gathering...")


# ---------------------------------------------------------------------------
# Strategy: Baker
# ---------------------------------------------------------------------------
def play_baker(client: httpx.Client, token: str, name: str, status: dict, turn: int):
    """Gather wheat, produce flour then bread."""
    # Gather wheat and berries
    resource = "wheat" if turn % 3 != 2 else "berries"
    result = api_call(client, "gather", {"resource": resource}, token)
    if result and "_error" not in result:
        log(name, f"Gathered {resource} (+{result.get('cash_earned', 0)} cash)")

    businesses = status.get("businesses", [])
    if not businesses and status["balance"] >= 250 and not status["housing"]["homeless"]:
        result = api_call(client, "register_business", {
            "name": f"{name}'s Mill",
            "type": "mill",
            "zone": "industrial",
        }, token)
        if result and "_error" not in result:
            biz_id = result.get("business_id")
            log(name, f"Registered mill! (id={biz_id})")
            if biz_id:
                api_call(client, "configure_production", {
                    "business_id": biz_id,
                    "product": "flour",
                }, token)

    if businesses:
        result = api_call(client, "work", {}, token)
        if result and "_error" not in result:
            log(name, f"Produced: {result.get('output_good', '?')}")

    # Sell flour/bread
    if turn % 4 == 0:
        for item in status.get("inventory", []):
            if item["good_slug"] in ("flour", "bread") and item["quantity"] >= 2:
                api_call(client, "marketplace_order", {
                    "action": "sell",
                    "product": item["good_slug"],
                    "quantity": item["quantity"] - 1,
                    "price": 12.0 if item["good_slug"] == "flour" else 25.0,
                }, token)
                log(name, f"Listed {item['good_slug']} on marketplace")


# ---------------------------------------------------------------------------
# Strategy: Trader
# ---------------------------------------------------------------------------
def play_trader(client: httpx.Client, token: str, name: str, status: dict, turn: int):
    """Buy cheap, sell high on marketplace."""
    # Gather for income floor
    result = api_call(client, "gather", {"resource": "berries"}, token)
    if result and "_error" not in result:
        log(name, f"Gathered berries (+{result.get('cash_earned', 0)} cash)")

    # Browse marketplace for deals
    goods_to_check = ["berries", "herbs", "wheat", "wood", "iron_ore"]
    good = goods_to_check[turn % len(goods_to_check)]

    browse = api_call(client, "marketplace_browse", {"product": good}, token)
    if browse and "_error" not in browse:
        asks = browse.get("asks", [])
        if asks:
            cheapest = asks[0]
            price = cheapest.get("price", 999)
            avail = cheapest.get("quantity_available", 0)
            if price <= 3.0 and avail > 0 and status["balance"] > price * min(avail, 5):
                buy_qty = min(avail, 5)
                api_call(client, "marketplace_order", {
                    "action": "buy",
                    "product": good,
                    "quantity": buy_qty,
                    "price": price + 0.5,
                }, token)
                log(name, f"Buying {buy_qty}x {good} @ {price + 0.5}")

    # Sell inventory at markup
    if turn % 3 == 0:
        for item in status.get("inventory", []):
            if item["quantity"] >= 3:
                api_call(client, "marketplace_order", {
                    "action": "sell",
                    "product": item["good_slug"],
                    "quantity": item["quantity"],
                    "price": 6.0,
                }, token)
                log(name, f"Listed {item['quantity']}x {item['good_slug']} @ 6.0")


# ---------------------------------------------------------------------------
# Strategy: Diversifier
# ---------------------------------------------------------------------------
def play_diversifier(client: httpx.Client, token: str, name: str, status: dict, turn: int):
    """Gather everything, sell variety."""
    all_resources = ["berries", "herbs", "cotton", "wheat", "wood", "stone", "clay", "sand", "iron_ore"]
    resource = all_resources[turn % len(all_resources)]

    result = api_call(client, "gather", {"resource": resource}, token)
    if result and "_error" not in result:
        log(name, f"Gathered {resource} (+{result.get('cash_earned', 0)} cash)")
    else:
        # Try next resource
        alt = all_resources[(turn + 1) % len(all_resources)]
        result = api_call(client, "gather", {"resource": alt}, token)
        if result and "_error" not in result:
            log(name, f"Gathered {alt} instead")

    # Sell when inventory builds up
    if turn % 4 == 0:
        for item in status.get("inventory", []):
            if item["quantity"] >= 4:
                api_call(client, "marketplace_order", {
                    "action": "sell",
                    "product": item["good_slug"],
                    "quantity": item["quantity"] - 1,
                    "price": 4.0,
                }, token)
                log(name, f"Listed {item['quantity']-1}x {item['good_slug']} @ 4.0")

    # Try to deposit savings
    if turn == 10 and status["balance"] > 100:
        api_call(client, "bank", {"action": "deposit", "amount": 50}, token)
        log(name, "Deposited 50 in bank")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
STRATEGIES = {
    "gatherer": play_gatherer,
    "industrialist": play_industrialist,
    "baker": play_baker,
    "trader": play_trader,
    "diversifier": play_diversifier,
}


def main():
    parser = argparse.ArgumentParser(description="Play the Agent Economy")
    parser.add_argument("name", help="Agent name")
    parser.add_argument("strategy", choices=STRATEGIES.keys(), help="Strategy to use")
    parser.add_argument("--turns", type=int, default=60, help="Number of turns to play")
    parser.add_argument("--interval", type=float, default=3.0, help="Seconds between turns")
    args = parser.parse_args()

    client = httpx.Client()
    strategy_fn = STRATEGIES[args.strategy]

    # Sign up
    token = signup(client, args.name)
    if not token:
        print(f"Failed to sign up as {args.name}")
        sys.exit(1)

    # Get initial status
    status = get_status(client, token, args.name)
    if not status:
        print(f"Failed to get status")
        sys.exit(1)

    log(args.name, f"Starting with balance={status['balance']}, strategy={args.strategy}")
    log(args.name, f"Housing: {'homeless' if status['housing']['homeless'] else status['housing'].get('zone_slug', 'housed')}")

    # Rent housing (cheapest zone)
    if status["housing"]["homeless"]:
        result = api_call(client, "rent_housing", {"zone": "outskirts"}, token)
        if result and "_error" not in result:
            log(args.name, f"Rented outskirts (rent={result['rent_cost_per_hour']}/hr)")
            status = get_status(client, token, args.name)
        else:
            log(args.name, f"Can't rent yet: {result.get('_message', '') if result else 'no response'}")

    # Main game loop
    for turn in range(args.turns):
        print(f"\n--- Turn {turn + 1}/{args.turns} ---")

        # Get current status
        status = get_status(client, token, args.name)
        if not status:
            log(args.name, "Can't get status, skipping turn")
            time.sleep(args.interval)
            continue

        bal = status["balance"]
        inv_count = sum(i["quantity"] for i in status.get("inventory", []))
        homeless = status["housing"]["homeless"]
        bankrupt = status.get("bankruptcy_count", 0)

        log(args.name, f"Balance: {bal:.2f} | Inventory: {inv_count} items | "
            f"{'HOMELESS' if homeless else 'housed'} | Bankruptcies: {bankrupt}")

        # Check for bankruptcy recovery
        if bankrupt > 0 and homeless and bal >= 0:
            log(args.name, "Recovering from bankruptcy...")
            result = api_call(client, "rent_housing", {"zone": "outskirts"}, token)
            if result and "_error" not in result:
                log(args.name, "Re-rented outskirts after bankruptcy")

        # Execute strategy
        try:
            strategy_fn(client, token, args.name, status, turn)
        except Exception as e:
            log(args.name, f"Strategy error: {e}")

        # Check messages
        if turn % 10 == 0:
            msgs = api_call(client, "messages", {"action": "list"}, token)
            if msgs and "_error" not in msgs:
                pending = msgs.get("count", msgs.get("pending", 0))
                if pending:
                    log(args.name, f"You have {pending} messages")

        time.sleep(args.interval)

    # Final status
    print(f"\n{'='*50}")
    status = get_status(client, token, args.name)
    if status:
        log(args.name, f"FINAL: balance={status['balance']:.2f}, "
            f"inventory={sum(i['quantity'] for i in status.get('inventory', []))}, "
            f"bankruptcies={status.get('bankruptcy_count', 0)}")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
