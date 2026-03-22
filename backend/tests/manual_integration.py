"""
Manual integration test — play the game as real agents would.
Tests every tool, every flow, every edge case.
"""
import asyncio
import json
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

# Set up path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Environment setup
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/agent_economy")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")
os.environ.setdefault("CONFIG_DIR", "/root/projects/agent-economy/config")

import httpx
import re

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from backend.clock import MockClock
from backend.config import load_settings, Settings, DatabaseSettings, RedisSettings
from backend.database import create_sessionmaker
from backend.main import create_app
from backend.models.base import Base

CONFIG_DIR = Path("/root/projects/agent-economy/config")

PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"
INFO = "\033[34m→\033[0m"

_test_count = 0
_pass_count = 0
_fail_count = 0
_failures = []


def ok(msg):
    global _test_count, _pass_count
    _test_count += 1
    _pass_count += 1
    print(f"  {PASS} {msg}")


def fail(msg, detail=""):
    global _test_count, _fail_count
    _test_count += 1
    _fail_count += 1
    _failures.append(f"{msg}: {detail}" if detail else msg)
    print(f"  {FAIL} {msg}")
    if detail:
        print(f"      Detail: {detail}")


def check(condition, msg, detail=""):
    if condition:
        ok(msg)
    else:
        fail(msg, detail)
    return condition


def _get_test_db_url():
    base_url = os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/agent_economy",
    )
    return re.sub(r"(/[^/]+)$", r"\1_test", base_url)


def _get_test_redis_url():
    base_url = os.environ.get("REDIS_URL", "redis://localhost:6379/1")
    if re.search(r"/\d+$", base_url):
        return re.sub(r"/\d+$", "/2", base_url)
    return base_url.rstrip("/") + "/2"


async def setup_test_db(db_url):
    engine = create_async_engine(db_url, echo=False)
    async with engine.begin() as conn:
        import backend.models  # noqa: F401
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    print(f"  {INFO} Test database created")


async def main():
    global _test_count, _pass_count, _fail_count, _failures

    print("\n" + "=" * 60)
    print("AGENT ECONOMY MANUAL INTEGRATION TESTS")
    print("=" * 60)

    # Setup
    test_db_url = _get_test_db_url()
    test_redis_url = _get_test_redis_url()

    print(f"\n{INFO} DB: {test_db_url.split('@')[-1]}")
    print(f"{INFO} Redis: {test_redis_url}")

    await setup_test_db(test_db_url)

    # Build settings
    s = load_settings(CONFIG_DIR)
    settings = Settings(
        database=DatabaseSettings(url=test_db_url, echo=False),
        redis=RedisSettings(url=test_redis_url),
        server=s.server,
        economy=s.economy,
        goods=s.goods,
        recipes=s.recipes,
        zones=s.zones,
        government=s.government,
        npc_demand=s.npc_demand,
        bootstrap=s.bootstrap,
    )

    # Start MockClock at current real time so agent.created_at (real DB time) is
    # chronologically before the clock. This lets us advance the clock forward past
    # eligibility gates (e.g., voting requires 14 days old).
    clock = MockClock(start=datetime.now(timezone.utc))
    app = create_app(settings=settings, clock=clock)

    async with app.router.lifespan_context(app):
        # Flush the test Redis DB
        redis = app.state.redis
        await redis.flushdb()
        print(f"  {INFO} Redis flushed")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:

            async def mcp_call(method, params=None, token=None):
                headers = {}
                if token:
                    headers["Authorization"] = f"Bearer {token}"
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": method, "arguments": params or {}},
                }
                r = await client.post("/mcp", json=payload, headers=headers)
                data = r.json()
                if "error" in data:
                    return {"error": data["error"], "_is_error": True}
                content = data.get("result", {}).get("content", [{}])
                if content and content[0].get("type") == "text":
                    try:
                        return json.loads(content[0]["text"])
                    except Exception:
                        return {"_raw": content[0]["text"]}
                return data.get("result", {})

            def is_error(result, code=None):
                if "_is_error" in result:
                    if code:
                        data = result["error"].get("data", {})
                        return isinstance(data, dict) and data.get("code") == code
                    return True
                # Also check for error_code pattern in success-shaped results
                if "error_code" in result:
                    if code:
                        return result.get("error_code") == code
                    return True
                return False

            def get_error_code(result):
                if "_is_error" in result:
                    data = result["error"].get("data", {})
                    if isinstance(data, dict):
                        return data.get("code")
                return result.get("error_code")

            # ============================================
            # TEST 1: Full Agent Lifecycle
            # ============================================
            print("\n=== TEST 1: Full Agent Lifecycle ===")

            # 1a. Sign up Alice
            result = await mcp_call("signup", {"name": "Alice", "model": "Claude Opus 4.6"})
            if check("action_token" in result, "Alice signed up", str(result)):
                alice_token = result["action_token"]
                alice_view_token = result.get("view_token")
                print(f"      action_token={alice_token[:12]}..., view_token={alice_view_token[:12] if alice_view_token else 'MISSING'}...")
            else:
                print("FATAL: Cannot continue without Alice's token")
                return

            # 1b. Check status — 0 balance, no housing
            status = await mcp_call("get_status", token=alice_token)
            check(not is_error(status), "get_status returns successfully", str(status))
            check(status.get("balance") == 0, "Alice starts with 0 balance", f"balance={status.get('balance')}")
            check(status.get("housing_zone") is None, "Alice has no housing initially", f"housing={status.get('housing_zone')}")
            print(f"      balance={status.get('balance')}, housing={status.get('housing_zone')}, inventory={status.get('inventory', [])}")

            # 1c. Try to rent housing with 0 balance — should fail
            result = await mcp_call("rent_housing", {"zone": "outskirts"}, token=alice_token)
            err_code = get_error_code(result)
            check(is_error(result), "Rent housing with 0 balance rejected", f"got: {result}")
            print(f"      Error code: {err_code}")

            # 1d. Gather berries (free action)
            result = await mcp_call("gather", {"resource": "berries"}, token=alice_token)
            check(not is_error(result), "Gathered berries successfully", str(result))
            print(f"      Gather result: {result}")

            # 1e. Try to gather again immediately — cooldown
            result = await mcp_call("gather", {"resource": "berries"}, token=alice_token)
            check(is_error(result, "COOLDOWN_ACTIVE"), "Cooldown enforced on rapid gather", str(result))

            # 1f. Advance clock past cooldown, gather again
            # Berries cooldown is 25s base, doubled to 50s when homeless
            clock.advance(60)
            result = await mcp_call("gather", {"resource": "berries"}, token=alice_token)
            check(not is_error(result), "Gathered after cooldown", str(result))
            print(f"      Second gather: {result}")

            # 1g. Check inventory
            status = await mcp_call("get_status", token=alice_token)
            inv = status.get("inventory", [])
            check(len(inv) > 0, "Alice has inventory after gathering", f"inventory={inv}")
            balance = status.get("balance", 0)
            print(f"      Balance: {balance}, Inventory: {inv}")

            # ============================================
            # TEST 2: Marketplace Trading
            # ============================================
            print("\n=== TEST 2: Marketplace Trading ===")

            # Sign up Bob
            bob_result = await mcp_call("signup", {"name": "Bob", "model": "GPT-5.4"})
            check("action_token" in bob_result, "Bob signed up", str(bob_result))
            bob_token = bob_result["action_token"]
            bob_view_token = bob_result.get("view_token")

            # Alice gathers more berries (advance 60s each time to clear 50s homeless cooldown)
            for i in range(5):
                clock.advance(60)
                r = await mcp_call("gather", {"resource": "berries"}, token=alice_token)
                if is_error(r):
                    print(f"      Gather {i+1} failed: {r}")

            # Check Alice's balance and inventory
            status = await mcp_call("get_status", token=alice_token)
            alice_balance = status.get("balance", 0)
            alice_inv = status.get("inventory", [])
            berry_qty = next((item["quantity"] for item in alice_inv if item.get("good_slug") == "berries"), 0)
            print(f"      Alice balance={alice_balance}, berries={berry_qty}")

            # Alice places sell order for berries
            if berry_qty >= 2:
                result = await mcp_call("marketplace_order", {
                    "action": "sell", "product": "berries", "quantity": 2, "price": 5
                }, token=alice_token)
                check(not is_error(result), "Alice places sell order", str(result))
                print(f"      Sell order: {result}")
            else:
                fail("Not enough berries to place sell order", f"berry_qty={berry_qty}")

            # Browse marketplace
            browse = await mcp_call("marketplace_browse", {"product": "berries"}, token=bob_token)
            check(not is_error(browse), "Marketplace browse works", str(browse))
            print(f"      Browse result keys: {list(browse.keys()) if isinstance(browse, dict) else type(browse)}")

            # Bob tries to buy with 0 balance
            result = await mcp_call("marketplace_order", {
                "action": "buy", "product": "berries", "quantity": 1, "price": 5
            }, token=bob_token)
            check(is_error(result, "INSUFFICIENT_FUNDS"), "Bob buy with 0 balance rejected", str(result))
            print(f"      Bob buy error: {get_error_code(result)}")

            # ============================================
            # TEST 3: Business Registration
            # ============================================
            print("\n=== TEST 3: Business Registration ===")

            # Alice tries to register business without housing
            result = await mcp_call("register_business", {
                "name": "Alice's Shop", "type": "general_store", "zone": "outskirts"
            }, token=alice_token)
            check(is_error(result), "Business registration without housing rejected", str(result))
            print(f"      Error code: {get_error_code(result)}")

            # ============================================
            # TEST 4: Messaging
            # ============================================
            print("\n=== TEST 4: Messaging ===")

            result = await mcp_call("messages", {
                "action": "send", "to_agent": "Bob", "text": "Hey Bob, want to trade berries?"
            }, token=alice_token)
            check(not is_error(result), "Alice sends message to Bob", str(result))
            print(f"      Send result: {result}")

            result = await mcp_call("messages", {"action": "read"}, token=bob_token)
            check(not is_error(result), "Bob reads messages", str(result))
            messages = result.get("messages", result.get("items", []))
            print(f"      Bob's messages: {messages}")
            check(len(messages) > 0, "Bob received Alice's message", f"messages={messages}")

            # Bob replies
            result = await mcp_call("messages", {
                "action": "send", "to_agent": "Alice", "text": "Sure! How many do you have?"
            }, token=bob_token)
            check(not is_error(result), "Bob replies to Alice", str(result))

            # ============================================
            # TEST 5: Economy Info
            # ============================================
            print("\n=== TEST 5: Economy Info ===")

            result = await mcp_call("get_economy", {"section": "government"}, token=alice_token)
            check(not is_error(result), "get_economy government section works", str(result))
            print(f"      Government info keys: {list(result.keys()) if isinstance(result, dict) else result}")

            result = await mcp_call("get_economy", {"section": "stats"}, token=alice_token)
            check(not is_error(result), "get_economy stats section works", str(result))
            print(f"      Stats: {result}")

            result = await mcp_call("get_economy", {"section": "market"}, token=alice_token)
            check(not is_error(result), "get_economy market section works", str(result))
            print(f"      Market info keys: {list(result.keys()) if isinstance(result, dict) else type(result)}")

            result = await mcp_call("get_economy", {"section": "zones"}, token=alice_token)
            check(not is_error(result), "get_economy zones section works", str(result))
            print(f"      Zones info keys: {list(result.keys()) if isinstance(result, dict) else result}")

            # ============================================
            # TEST 6: Voting
            # ============================================
            print("\n=== TEST 6: Voting ===")

            result = await mcp_call("vote", {"government_type": "libertarian"}, token=alice_token)
            check(is_error(result, "NOT_ELIGIBLE"), "Vote rejected (too young/not eligible)", str(result))
            print(f"      Vote error: {get_error_code(result)}")

            # Advance 2 weeks + 1 hour to be safely past eligibility
            # agent.created_at is real DB time; clock started at real time too, so this works
            clock.advance(14 * 24 * 3600 + 3600)
            result = await mcp_call("vote", {"government_type": "libertarian"}, token=alice_token)
            check(not is_error(result), "Vote succeeds after 2 weeks", str(result))
            print(f"      Vote after 2 weeks: {'OK' if not is_error(result) else get_error_code(result)} -> {result}")

            # ============================================
            # TEST 7: Banking
            # ============================================
            print("\n=== TEST 7: Banking ===")

            result = await mcp_call("bank", {"action": "view_balance"}, token=alice_token)
            check(not is_error(result), "Bank view_balance works", str(result))
            print(f"      Balance: {result}")

            result = await mcp_call("bank", {"action": "deposit", "amount": 10}, token=alice_token)
            print(f"      Deposit attempt: {'OK' if not is_error(result) else get_error_code(result)} -> {result}")

            result = await mcp_call("bank", {"action": "take_loan", "amount": 100}, token=alice_token)
            print(f"      Loan attempt: {'OK' if not is_error(result) else get_error_code(result)} -> {result}")

            # Check loan state
            result = await mcp_call("bank", {"action": "view_balance"}, token=alice_token)
            print(f"      Bank after loan: {result}")

            # Test withdraw from empty account (should fail)
            result_withdraw = await mcp_call("bank", {"action": "withdraw", "amount": 10}, token=alice_token)
            print(f"      Withdraw attempt: {'OK' if not is_error(result_withdraw) else get_error_code(result_withdraw)}")
            # Note: loan repayment is automatic (24 hourly installments), no manual repay action

            # ============================================
            # TEST 8: Direct Trade
            # ============================================
            print("\n=== TEST 8: Direct Trade ===")

            # Alice proposes trade to Bob
            result = await mcp_call("trade", {
                "action": "propose",
                "target_agent": "Bob",
                "offer_items": [{"good_slug": "berries", "quantity": 1}],
                "request_items": [],
                "offer_money": 0,
                "request_money": 0
            }, token=alice_token)
            check(not is_error(result), "Alice proposes trade", str(result))
            print(f"      Trade proposal: {result}")
            # trade_id is nested under result["trade"]["id"]
            trade_id = result.get("trade", {}).get("id") if isinstance(result.get("trade"), dict) else None
            print(f"      Trade ID: {trade_id}")

            # Bob accepts trade (if we have trade_id)
            # Note: trade tool only supports 'propose', 'respond', 'cancel' — no 'list' action
            if trade_id:
                result = await mcp_call("trade", {
                    "action": "respond", "trade_id": trade_id, "accept": True
                }, token=bob_token)
                print(f"      Bob responds to trade: {'OK' if not is_error(result) else get_error_code(result)} -> {result}")

            # Test cancel action: Alice proposes another trade with berries and then cancels
            # (empty trades are invalid — must have at least one item or money)
            result_empty_trade = await mcp_call("trade", {
                "action": "propose",
                "target_agent": "Bob",
                "offer_items": [],
                "request_items": [],
                "offer_money": 0,
                "request_money": 0
            }, token=alice_token)
            check(is_error(result_empty_trade), "Empty trade proposal rejected", str(result_empty_trade))

            # Propose a real trade (offer berries, request nothing — a gift)
            alice_status2 = await mcp_call("get_status", token=alice_token)
            alice_berries2 = next((i["quantity"] for i in alice_status2.get("inventory", []) if i["good_slug"] == "berries"), 0)
            if alice_berries2 >= 1:
                result2 = await mcp_call("trade", {
                    "action": "propose",
                    "target_agent": "Bob",
                    "offer_items": [{"good_slug": "berries", "quantity": 1}],
                    "request_items": [],
                    "offer_money": 0,
                    "request_money": 0
                }, token=alice_token)
                check(not is_error(result2), "Alice proposes trade to cancel", str(result2))
                trade2_id = result2.get("trade", {}).get("id")
                if trade2_id:
                    cancel_result = await mcp_call("trade", {
                        "action": "cancel", "trade_id": trade2_id
                    }, token=alice_token)
                    check(not is_error(cancel_result), "Alice cancels trade proposal", str(cancel_result))
                    print(f"      Cancel result: {cancel_result}")

            # ============================================
            # TEST 9: Jobs
            # ============================================
            print("\n=== TEST 9: Jobs ===")

            result = await mcp_call("list_jobs", {}, token=alice_token)
            check(not is_error(result), "list_jobs works", str(result))
            print(f"      Jobs: {result}")

            result = await mcp_call("work", {}, token=alice_token)
            print(f"      Work attempt: {'OK' if not is_error(result) else get_error_code(result)} -> {result}")

            # ============================================
            # TEST 10: Error Edge Cases
            # ============================================
            print("\n=== TEST 10: Edge Cases ===")

            # Duplicate signup
            result = await mcp_call("signup", {"name": "Alice"})
            check(is_error(result, "ALREADY_EXISTS"), "Duplicate signup rejected", str(result))

            # Invalid tool name
            payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "nonexistent_tool", "arguments": {}}}
            r = await client.post("/mcp", json=payload, headers={"Authorization": f"Bearer {alice_token}"})
            data = r.json()
            check("error" in data, "Invalid tool name returns error", str(data))
            print(f"      Invalid tool error: {data.get('error', {}).get('message', '')[:80]}")

            # No auth on protected tool
            result = await mcp_call("get_status")  # no token
            check(is_error(result), "Auth required for get_status", str(result))

            # Gather non-gatherable good
            result = await mcp_call("gather", {"resource": "bread"}, token=alice_token)
            check(is_error(result), "Gather non-gatherable good rejected", str(result))
            print(f"      Gather bread error: {get_error_code(result)}")

            # Invalid JSON-RPC method
            payload = {"jsonrpc": "2.0", "id": 1, "method": "invalid/method", "params": {}}
            r = await client.post("/mcp", json=payload)
            data = r.json()
            check("error" in data, "Invalid method returns error", str(data))

            # ============================================
            # TEST 11: MCP Protocol
            # ============================================
            print("\n=== TEST 11: MCP Protocol ===")

            # Initialize
            r = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            init = r.json()
            check("result" in init, "Initialize returns result", str(init))
            check("serverInfo" in init.get("result", {}), "Initialize has serverInfo", str(init))
            print(f"      serverInfo: {init.get('result', {}).get('serverInfo', {})}")

            # Tools list
            r = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
            tools = r.json()
            tool_names = [t["name"] for t in tools.get("result", {}).get("tools", [])]
            check(len(tool_names) == 18, f"tools/list has 18 tools", f"got {len(tool_names)}: {tool_names}")
            print(f"      Tools ({len(tool_names)}): {', '.join(tool_names)}")

            # ============================================
            # TEST 12: REST API Dashboard Endpoints
            # ============================================
            print("\n=== TEST 12: REST API Dashboard Endpoints ===")

            r = await client.get("/api/stats")
            check(r.status_code == 200, "/api/stats returns 200", f"status={r.status_code}, body={r.text[:200]}")
            print(f"      stats keys: {list(r.json().keys())}")

            r = await client.get("/api/leaderboards")
            check(r.status_code == 200, "/api/leaderboards returns 200", f"status={r.status_code}, body={r.text[:200]}")
            print(f"      leaderboards keys: {list(r.json().keys())}")

            r = await client.get("/api/zones")
            check(r.status_code == 200, "/api/zones returns 200", f"status={r.status_code}, body={r.text[:200]}")
            print(f"      zones: {r.json()[:1] if isinstance(r.json(), list) else list(r.json().keys())}")

            r = await client.get("/api/government")
            check(r.status_code == 200, "/api/government returns 200", f"status={r.status_code}, body={r.text[:200]}")
            print(f"      government keys: {list(r.json().keys())}")

            r = await client.get("/api/goods")
            check(r.status_code == 200, "/api/goods returns 200", f"status={r.status_code}, body={r.text[:200]}")
            print(f"      goods count: {len(r.json()) if isinstance(r.json(), list) else 'dict'}")

            # Private endpoints with view token (Alice's)
            r = await client.get(f"/api/agent?token={alice_view_token}")
            check(r.status_code == 200, "/api/agent with valid view_token returns 200", f"status={r.status_code}, body={r.text[:300]}")
            print(f"      /api/agent keys: {list(r.json().keys()) if isinstance(r.json(), dict) else type(r.json())}")

            r = await client.get(f"/api/agent/transactions?token={alice_view_token}")
            check(r.status_code == 200, "/api/agent/transactions returns 200", f"status={r.status_code}, body={r.text[:200]}")
            print(f"      transactions: {r.json()}")

            r = await client.get(f"/api/agent/businesses?token={alice_view_token}")
            check(r.status_code == 200, "/api/agent/businesses returns 200", f"status={r.status_code}, body={r.text[:200]}")
            print(f"      businesses: {r.json()}")

            r = await client.get(f"/api/agent/messages?token={alice_view_token}")
            check(r.status_code == 200, "/api/agent/messages returns 200", f"status={r.status_code}, body={r.text[:200]}")
            print(f"      messages count: {len(r.json()) if isinstance(r.json(), list) else r.json()}")

            # Invalid view token
            r = await client.get("/api/agent?token=invalidtoken123")
            check(r.status_code in (401, 403, 404), "Invalid view token returns auth error", f"status={r.status_code}")
            print(f"      Invalid token status: {r.status_code}")

            # ============================================
            # TEST 13: Health endpoint
            # ============================================
            print("\n=== TEST 13: Health Endpoint ===")
            r = await client.get("/health")
            check(r.status_code == 200, "/health returns 200", f"status={r.status_code}")
            check(r.json().get("status") == "ok", "/health returns {status: ok}", str(r.json()))

            # ============================================
            # TEST 14: Signed-up agent's advanced actions
            # ============================================
            print("\n=== TEST 14: Advanced Agent Actions ===")

            # Sign up Charlie with proper model
            charlie_result = await mcp_call("signup", {"name": "Charlie", "model": "Gemini 2.0"})
            check("action_token" in charlie_result, "Charlie signed up", str(charlie_result))
            charlie_token = charlie_result["action_token"]
            charlie_view_token = charlie_result.get("view_token")

            # Charlie gathers lots of resources
            for i in range(10):
                clock.advance(35)
                await mcp_call("gather", {"resource": "berries"}, token=charlie_token)

            charlie_status = await mcp_call("get_status", token=charlie_token)
            print(f"      Charlie balance={charlie_status.get('balance')}, inventory={charlie_status.get('inventory')}")

            # Check storage info in status
            storage = charlie_status.get("storage", {})
            check("used" in storage, "Status includes storage info", str(storage))
            check("capacity" in storage, "Status includes capacity", str(storage))
            print(f"      Storage: used={storage.get('used')}, capacity={storage.get('capacity')}")

            # Test configure_production (requires business ownership)
            result = await mcp_call("configure_production", {
                "business_id": 9999, "recipe_slug": "bread"
            }, token=charlie_token)
            check(is_error(result), "configure_production without business rejected", str(result))
            print(f"      configure_production error: {get_error_code(result)}")

            # Test set_prices (requires business ownership)
            result = await mcp_call("set_prices", {
                "business_id": 9999, "good_slug": "bread", "price": 10
            }, token=charlie_token)
            check(is_error(result), "set_prices without business rejected", str(result))
            print(f"      set_prices error: {get_error_code(result)}")

            # Test manage_employees (requires business)
            result = await mcp_call("manage_employees", {
                "action": "fire", "business_id": 9999, "agent_name": "Bob"
            }, token=charlie_token)
            check(is_error(result), "manage_employees without business rejected", str(result))
            print(f"      manage_employees error: {get_error_code(result)}")

            # Apply for nonexistent job
            result = await mcp_call("apply_job", {
                "job_id": 9999
            }, token=charlie_token)
            check(is_error(result), "apply_job for nonexistent job rejected", str(result))
            print(f"      apply_job error: {get_error_code(result)}")

            # ============================================
            # TEST 15: Market order cancellation
            # ============================================
            print("\n=== TEST 15: Market Order Cancellation ===")

            # Alice places another sell order
            alice_status = await mcp_call("get_status", token=alice_token)
            alice_inv = alice_status.get("inventory", [])
            berry_qty = next((item["quantity"] for item in alice_inv if item.get("good_slug") == "berries"), 0)
            print(f"      Alice berries: {berry_qty}")

            if berry_qty >= 1:
                result = await mcp_call("marketplace_order", {
                    "action": "sell", "product": "berries", "quantity": 1, "price": 10
                }, token=alice_token)
                check(not is_error(result), "Alice places sell order for cancellation test", str(result))
                # order id is nested under result["order"]["id"]
                order_id = result.get("order", {}).get("id") if isinstance(result.get("order"), dict) else None
                print(f"      Order ID: {order_id}")

                if order_id:
                    cancel_result = await mcp_call("marketplace_order", {
                        "action": "cancel", "order_id": order_id
                    }, token=alice_token)
                    check(not is_error(cancel_result), "Alice cancels sell order", str(cancel_result))
                    print(f"      Cancel result: {cancel_result}")
                else:
                    fail("Could not extract order_id from sell result", str(result))
            else:
                print(f"      Skipping (no berries)")

            # ============================================
            # TEST 16: Messages - inbox/outbox
            # ============================================
            print("\n=== TEST 16: Messages Inbox/Outbox ===")

            result = await mcp_call("messages", {"action": "read"}, token=alice_token)
            check(not is_error(result), "Read inbox works", str(result))
            msgs = result.get("messages", [])
            print(f"      Inbox ({len(msgs)} messages): {[m.get('text', '')[:30] for m in msgs]}")

            # Page 2 (empty — we don't have 20+ messages)
            result2 = await mcp_call("messages", {"action": "read", "page": 2}, token=alice_token)
            check(not is_error(result2), "Read inbox page 2 works", str(result2))
            print(f"      Inbox page 2: {len(result2.get('messages', []))} messages")

            # ============================================
            # TEST 17: marketplace_browse various products
            # ============================================
            print("\n=== TEST 17: Marketplace Browse ===")

            for product in ["berries", "bread", "wood"]:
                result = await mcp_call("marketplace_browse", {"product": product}, token=alice_token)
                check(not is_error(result), f"Browse {product}", str(result)[:100])

            # Browse all (no product filter)
            result = await mcp_call("marketplace_browse", {}, token=alice_token)
            check(not is_error(result), "Browse all products", str(result)[:100])
            print(f"      Browse all keys: {list(result.keys()) if isinstance(result, dict) else type(result)}")

            # ============================================
            # SUMMARY
            # ============================================
            print("\n" + "=" * 60)
            print(f"RESULTS: {_pass_count} passed, {_fail_count} failed, {_test_count} total")
            if _failures:
                print("\nFAILURES:")
                for f in _failures:
                    print(f"  - {f}")
            else:
                print("ALL TESTS PASSED")
            print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
