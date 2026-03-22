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
| Slow | 1h | Rent, food, taxes, loans, audits, NPC businesses, bankruptcy |
| Daily | 24h | Price history downsampling, economy snapshots |
| Weekly | 7d | Election tally, government template update |
