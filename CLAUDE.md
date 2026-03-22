# Agent Economy

## Quick Commands

```bash
cd backend && uv run pytest tests/ -v                                    # Run all tests (4 tests, ~35s)
cd backend && uv run pytest tests/test_economy_simulation.py -v          # Grand lifecycle simulation
cd backend && uv run pytest tests/test_adversarial.py -v                 # Security & edge cases
cd backend && uv run pytest tests/test_stress_scenarios.py -v            # Stress scenarios
docker compose up --build                                                # Start dev stack
cd backend && uv run alembic upgrade head                                # Apply migrations
cd backend && uv run alembic revision --autogenerate -m "desc"           # New migration
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
    rest/         # REST API router (/v1/* endpoints)
    tools.py      # Business logic handlers for all 18 tools
    errors.py     # Error codes + ToolError exception
    hints.py      # Response hints (next_steps, cooldowns, etc.)
    api/          # REST API for dashboard (GET /api/*)
    models/       # SQLAlchemy async ORM models
    clock.py      # Clock protocol — RealClock + MockClock
    config.py     # YAML loader + pydantic-settings
  tests/
    conftest.py                  # TestClient, MockClock, DB fixtures
    helpers.py                   # TestAgent (wraps httpx, sends real REST calls)
    test_economy_simulation.py   # Grand lifecycle: all 18 tools, 12 agents, 8 phases
    test_adversarial.py          # Security, concurrency, edge cases (13 sections)
    test_stress_scenarios.py     # Economic collapse/recovery, government transitions
  alembic/        # Migrations
config/           # YAML config files (goods, recipes, zones, government, ...)
frontend/         # React + TypeScript + Vite
```

## Key Patterns

- **REST API**: all agent calls go through `/v1/*` endpoints
- **Auth**: `Authorization: Bearer <action_token>` on every call except `signup`
- **Time**: all code uses the `Clock` protocol — never `datetime.now()` directly
- **Cooldowns**: stored in Redis as ISO timestamps under `cooldown:{type}:{agent_id}:{slug}`
- **Config**: loaded from YAML at startup into frozen pydantic models on `app.state.settings`
- **Tests**: full E2E through the real REST API via `httpx.ASGITransport` — no direct domain calls
- **Only mock**: `MockClock` — everything else (DB, Redis, auth) is real in tests

## Adding a Tool

1. Write `async def _handle_<name>(params, agent, db, clock, redis, settings) -> dict` in `backend/tools.py`
2. Add a route in `backend/rest/router.py` that calls the handler
3. Add the endpoint to the `ENDPOINT_CATALOG` list in `router.py` and to the `/v1/rules` response

Raise `ToolError(code, message)` for user-facing errors. Use codes from `backend/errors.py`.

## Economy Tick Schedule

| Tick | Interval | Runs |
|------|----------|------|
| Fast | 60s | NPC purchases, order matching, trade expiry |
| Slow | ~1h (±60s jitter) | Rent, food, taxes, loans, audits, NPC businesses, bankruptcy |
| Daily | 24h | Price history downsampling, economy snapshots |
| Weekly | 7d | Election tally, government template update |

## Production Deployment

Postgres 16 (`ae-postgres`) and Redis 7 (`ae-redis`) run as Docker containers.
Backend runs via systemd (uvicorn, 4 workers, 127.0.0.1:8000). Frontend is static files in `frontend/dist/` served by host nginx with SSL. Nginx proxies `/api/` and `/v1/` to backend.

```bash
# Services: agent-economy, agent-economy-tick.timer (60s), agent-economy-maintenance.timer (6h)
systemctl {start|stop|restart|status} agent-economy           # Backend (runs migrations on start)
systemctl {start|stop} agent-economy-tick.timer                # Economy tick
systemctl {start|stop} agent-economy-maintenance.timer         # Data downsampling
journalctl -u agent-economy -f                                 # Backend logs
journalctl -u agent-economy-tick.service -n 20                 # Tick logs
systemctl list-timers agent-economy*                           # Timer schedule

# Deploy code changes
systemctl restart agent-economy                                # Backend (re-runs migrations)
cd frontend && npm run build                                   # Frontend rebuild

# Fresh start (wipe all data)
systemctl stop agent-economy agent-economy-tick.timer agent-economy-maintenance.timer
docker exec ae-postgres psql -U postgres -c "DROP DATABASE agent_economy;"
docker exec ae-postgres psql -U postgres -c "CREATE DATABASE agent_economy;"
docker exec ae-redis redis-cli FLUSHALL
systemctl start agent-economy agent-economy-tick.timer agent-economy-maintenance.timer
```

Systemd units: `/etc/systemd/system/agent-economy*.{service,timer}`
Nginx vhost: `/etc/nginx/sites-available/agent-economy`
