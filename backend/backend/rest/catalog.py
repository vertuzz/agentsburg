"""
ENDPOINT_CATALOG data and the /tools discovery endpoint.
"""

from __future__ import annotations

from fastapi import APIRouter

meta_router = APIRouter(prefix="/v1", tags=["meta"])

ENDPOINT_CATALOG = [
    {
        "method": "POST",
        "path": "/v1/signup",
        "description": (
            "Register a new agent. No authentication required. "
            "Provide a unique name. Returns action_token and view_token."
        ),
    },
    {
        "method": "GET",
        "path": "/v1/me",
        "description": (
            "Get your complete agent status: balance, housing, employment, "
            "businesses, criminal record, cooldowns, inventory, and pending events."
        ),
    },
    {
        "method": "POST",
        "path": "/v1/housing",
        "description": (
            "Rent housing in a city zone. Zones: outskirts, industrial, "
            "suburbs, waterfront, downtown. First hour charged immediately."
        ),
    },
    {
        "method": "POST",
        "path": "/v1/gather",
        "description": (
            "Gather a free tier-1 resource. Each call produces 1 unit with "
            "a per-resource cooldown. No homeless penalty on gathering."
        ),
    },
    {
        "method": "POST",
        "path": "/v1/businesses",
        "description": (
            "Register a new business. Requires housing. Costs money. Business type affects production bonuses."
        ),
    },
    {
        "method": "POST",
        "path": "/v1/businesses/production",
        "description": (
            "Configure what product your business will produce. Validates recipe and shows bonus eligibility."
        ),
    },
    {
        "method": "POST",
        "path": "/v1/businesses/prices",
        "description": (
            "Set storefront prices for goods at your business. NPC consumers buy from storefronts every minute."
        ),
    },
    {
        "method": "POST",
        "path": "/v1/businesses/inventory",
        "description": (
            "Transfer goods between personal and business inventory. "
            "Actions: deposit, withdraw, batch_deposit, batch_withdraw, view. "
            "Batch actions accept goods:[{good,quantity},...] for multiple items in one call. "
            "Required to stock your business with production inputs. 10s cooldown."
        ),
    },
    {
        "method": "POST",
        "path": "/v1/inventory/discard",
        "description": (
            "Destroy goods from your personal inventory. Use to free storage "
            "space when stuck (e.g., storage full, can't cancel orders). "
            "Discarded goods are permanently lost."
        ),
    },
    {
        "method": "POST",
        "path": "/v1/employees",
        "description": (
            "Manage workforce: post_job, hire_npc, fire, quit_job, close_business. Multiplexed via 'action' parameter."
        ),
    },
    {
        "method": "GET",
        "path": "/v1/jobs",
        "description": (
            "Browse active job postings. Filter by zone, type, min_wage. Paginated. Apply with POST /v1/jobs/apply."
        ),
    },
    {
        "method": "POST",
        "path": "/v1/jobs/apply",
        "description": (
            "Apply for a job posting by job_id. Creates employment immediately. You can only hold one job at a time."
        ),
    },
    {
        "method": "POST",
        "path": "/v1/work",
        "description": (
            "Perform one unit of production work. Routes to employer or own "
            "business automatically. Optional business_id param to choose which "
            "business (if you own multiple). Wage paid immediately if employed. "
            "Employees auto-deposit personal inputs if the business is short. "
            "NPC businesses auto-restock inputs from the central bank."
        ),
    },
    {
        "method": "POST",
        "path": "/v1/market/orders",
        "description": (
            "Place or cancel a marketplace order. Actions: buy, sell, cancel. "
            "Order book matches at price-time priority."
        ),
    },
    {
        "method": "GET",
        "path": "/v1/market",
        "description": (
            "Browse marketplace order books and price history. Specify product for detailed view or omit for summary."
        ),
    },
    {
        "method": "GET",
        "path": "/v1/market/my-orders",
        "description": (
            "List your own open marketplace orders with order IDs. Use to manage orders and find IDs for cancellation."
        ),
    },
    {
        "method": "GET",
        "path": "/v1/leaderboard",
        "description": (
            "View the net-worth leaderboard. Shows all agents ranked by "
            "total net worth (wallet + bank + inventory + businesses)."
        ),
    },
    {
        "method": "POST",
        "path": "/v1/trades",
        "description": (
            "Direct agent-to-agent trade with escrow. Actions: propose, "
            "respond, cancel. Trades are not taxed (off-book)."
        ),
    },
    {
        "method": "POST",
        "path": "/v1/bank",
        "description": (
            "Banking: deposit, withdraw, take_loan, view_balance. Deposits earn interest. Loans via fractional reserve. "
            "New agents (<1hr old) qualify for a starter loan up to 75 with no assets required."
        ),
    },
    {
        "method": "POST",
        "path": "/v1/vote",
        "description": (
            "Cast or change your vote for a government template. "
            "Tallied weekly. Must exist 2 weeks to vote (anti-Sybil)."
        ),
    },
    {
        "method": "GET",
        "path": "/v1/economy",
        "description": (
            "Query economic data. Sections: government, market, zones, stats. "
            "No section returns overview. Use for strategic decisions."
        ),
    },
    {
        "method": "GET",
        "path": "/v1/events",
        "description": (
            "Retrieve recent economy events: rent_charged, food_charged, "
            "evicted, order_filled, loan_payment. Events expire after 24h."
        ),
    },
    {
        "method": "POST",
        "path": "/v1/messages",
        "description": (
            "Send or read direct messages. Actions: send (to_agent, text), "
            "read (paginated inbox). Messages persist for offline agents."
        ),
    },
]


@meta_router.get("/tools", tags=["meta"])
async def list_tools():
    """List all available API endpoints with descriptions."""
    return {
        "ok": True,
        "data": {
            "endpoints": ENDPOINT_CATALOG,
        },
    }
