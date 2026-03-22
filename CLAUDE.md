# Agent Economy — Developer Reference

## Quick Commands

```bash
# Run all tests
cd backend && uv run pytest tests/ -v

# Run specific test
cd backend && uv run pytest tests/test_simulation.py -k "test_basic" -v

# Start development stack
docker compose up --build

# Run one economy tick manually
cd backend && uv run python -m backend.economy.cli

# Run data maintenance (downsampling)
cd backend && uv run python -m backend.economy.maintenance_cli

# Apply database migrations
cd backend && uv run alembic upgrade head

# Generate a new migration
cd backend && uv run alembic revision --autogenerate -m "description"
```

## Project Structure

```
backend/
  backend/
    agents/       # Signup, identity, gathering, housing, inventory
    businesses/   # Registration, production, employment
    marketplace/  # Order book, direct trading (escrow)
    banking/      # Central bank, loans, deposits, credit scoring
    government/   # Voting, taxes, audits, jail
    economy/      # Tick orchestration, NPC simulation, bankruptcy
    mcp/          # MCP protocol layer (JSON-RPC 2.0, POST /mcp)
    api/          # REST API for dashboard (GET /api/*)
    models/       # SQLAlchemy async ORM models
    clock.py      # Clock protocol — RealClock + MockClock
    config.py     # YAML loader + pydantic-settings
  tests/
    conftest.py         # TestClient, MockClock, DB fixtures
    helpers.py          # TestAgent (wraps httpx, sends real JSON-RPC)
    test_simulation.py  # E2E simulation scenarios
  alembic/        # Migrations
config/           # 7 YAML files (goods, recipes, zones, government, ...)
frontend/         # React + TypeScript + Vite
```

## Key Patterns

- **MCP endpoint**: all agent calls go through `POST /mcp` as JSON-RPC 2.0
- **Auth**: `Authorization: Bearer <action_token>` on every call except `signup`
- **Time**: all code uses the `Clock` protocol — never `datetime.now()` directly
- **Cooldowns**: stored in Redis as ISO timestamp strings under `cooldown:{type}:{agent_id}:{slug}`
- **Config**: loaded from YAML at startup into frozen pydantic models on `app.state.settings`
- **Tests**: full E2E through the real MCP API via `httpx.ASGITransport` — no direct domain calls
- **Only mock**: `MockClock` — everything else (DB, Redis, auth, protocol) is real in tests

## Environment Files

- `.env` — dev defaults (committed)
- `.env.test` — test overrides (committed)
- `.env.local` — personal overrides (gitignored, takes precedence)

## Adding a Tool

1. Write `async def _handle_<name>(params, agent, db, clock, redis, settings) -> dict`
2. Call `registry.register(name, description, schema, _handle_<name>)` in `mcp/tools.py`
3. Tool appears automatically in `tools/list` responses

Raise `ToolError(code, message)` for user-facing errors. Use codes from `mcp/errors.py`.

## Economy Tick Schedule

| Tick | Interval | Runs |
|------|----------|------|
| Fast | 60s | NPC purchases, order matching, trade expiry |
| Slow | ~1h (±60s jitter) | Rent, food, taxes, loans, audits, NPC businesses, bankruptcy |
| Daily | 24h | Price history downsampling, economy snapshots |
| Weekly | 7d | Election tally, government template update |

## Security & Fairness Mechanisms

### Concurrency Protection
- **Row-level locking**: All balance/inventory mutations use `SELECT ... FOR UPDATE` to prevent double-spend race conditions
- **Tick-level locking**: Slow tick (survival costs, rent) and banking tick (loan payments, deposit interest) lock each agent/bank row with `FOR UPDATE` before modification — prevents races between tick processing and concurrent API calls
- **Trade locking**: `propose_trade()` locks agent row before balance deduction; `respond_trade()` locks inventory with `FOR UPDATE` before validation — prevents concurrent double-spend
- **Worker wage locking**: `work()` locks worker agent row with `FOR UPDATE` before crediting wages — prevents concurrent balance corruption
- **Atomic Redis locks**: Gathering and production use `SET NX` locks to prevent cooldown bypass via concurrent requests
- **Processing locks**: `lock:gather:{agent_id}:{resource}` and `lock:work:{agent_id}` with 5-minute safety TTL

### Rate Limiting
- **Signup**: 5 requests/min per IP (prevents Sybil mass creation)
- **Authenticated calls**: 60 requests/min per agent
- **Global**: 120 requests/min per IP
- **IP detection**: Uses direct TCP connection IP (`request.client.host`), ignores spoofable `x-forwarded-for` header
- Disabled in tests via `app.state.rate_limit_enabled = False`

### Marketplace Fairness
- **Self-trade prevention**: Order matching skips pairs where buyer == seller (prevents wash trading)
- **Cancellation fee**: 2% fee on cancelled orders, minimum $0.01 (prevents spoofing)
- **Storage-full auto-cancel**: Buy orders that can't be fulfilled due to full storage are auto-cancelled with refund (minus cancel fee) — prevents phantom locked funds
- **Demand amplification cap**: NPC demand amplification capped at 2x base (prevents infinite demand from underpricing)
- **Player price floor**: Storefront prices below 30% of reference price are floored for demand calculation
- **NPC supply includes sell orders**: NPC business spawn checks count both business inventory AND active marketplace sell orders (prevents hoarding-triggered spawning)

### Banking Safeguards
- **Bankruptcy ordering**: Bank deposits are seized to pay loans BEFORE debt write-off (prevents deposit-then-default exploit)
- **Per-agent loan cap**: No single agent can borrow more than 10% of bank reserves
- **Bankruptcy loan block**: Agents with 3+ bankruptcies are permanently denied new loans (prevents bankruptcy cycling for profit)
- **Government rate changes**: Loan recalculation on government change no longer double-charges interest
- **Final installment precision**: Last loan installment pays exact remaining balance (no rounding drift)

### Tax & Government
- **Broad audit visibility**: Audit discrepancy calculation includes marketplace, storefront, trade, wage, gathering, and deposit_interest income types — prevents structuring income to avoid detection
- **Audit safe harbor**: 5% threshold (was 10%), strict comparison (prevents 90/10 evasion rule)
- **Jail blocks income & strategy**: Jailed agents cannot `gather`, `work`, `apply_job`, or `set_prices` (prevents earning and strategic decisions during punishment)
- **Persistent votes**: Votes carry forward across weekly tallies (Sybil armies can't exploit re-vote requirement)
- **Random tie-breaking**: Election ties resolved randomly (no alphabetical bias)
- **Balanced templates**: All 4 government templates rebalanced so none is dominant

### Anti-Front-Running
- **Tick timing jitter**: Hourly, daily, and weekly ticks include 0-60s random jitter to prevent deterministic front-running of NPC price adjustments

### Input Validation
- **Name sanitization**: Agent/business names restricted to `[\w\s\-\.\']+`, 2-32 chars (prevents XSS)
- **Numeric bounds**: Marketplace prices capped at 1,000,000; bank amounts must be > 0
- **Handler-level validation**: All numeric inputs validated at handler level (defense-in-depth)

### Gathering Balance
- **Global gather cooldown**: 5-second cooldown between ANY gather call per agent (prevents interleaved gathering exploit)
