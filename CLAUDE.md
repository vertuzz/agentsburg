# Agent Economy

## Quick Commands

```bash
cd backend && uv run pytest tests/ -v                                    # Run all tests (4 test files, 8 tests, ~35s)
cd backend && uv run pytest tests/test_economy_simulation.py -v          # Grand lifecycle simulation
cd backend && uv run pytest tests/test_adversarial.py -v                 # Security & edge cases
cd backend && uv run pytest tests/test_stress_scenarios.py -v            # Stress scenarios
cd backend && uv run pytest tests/test_spectator.py -v                   # Spectator experience
docker compose up --build                                                # Start dev stack
cd backend && uv run alembic upgrade head                                # Apply migrations
cd backend && uv run alembic revision --autogenerate -m "desc"           # New migration
cd backend && uv run ruff check --fix backend/ tests/                    # Backend lint (auto-fix)
cd backend && uv run ruff format backend/ tests/                         # Backend format
cd frontend && npm run lint:fix                                          # Frontend lint + format (auto-fix)
```

## Project Structure

```
backend/
  backend/
    agents/       # Signup, identity, gathering, housing, inventory
    handlers/     # Domain handler modules (agents, banking, businesses, etc.)
    businesses/   # Registration, production, employment, jobs, workers, recipes
    marketplace/  # Order book (matching, browsing), trading (escrow, trade_responses)
    banking/      # Central bank, loans, deposits, credit scoring, helpers
    government/   # Voting, taxes, auditing, jail
    economy/      # Tick orchestration, NPC simulation, bankruptcy, seeds, snapshots
    spectator/    # Spectator experience — event feed, narrative, strategy, badges, commentary, conflicts
    rest/         # REST API router — common, routes_core, routes_economy, catalog, rules
    tools.py      # Re-export layer for handlers/ (backwards compat)
    errors.py     # Error codes + ToolError exception
    hints.py      # Response hints (next_steps, cooldowns, etc.)
    api/          # Dashboard API — stats, agents, businesses, market, world, dashboard, feed
    models/       # SQLAlchemy async ORM models
    clock.py      # Clock protocol — RealClock + MockClock
    config.py     # YAML loader + pydantic-settings
    main.py       # FastAPI application entry point
    database.py   # Async SQLAlchemy engine + session factory
    redis.py      # Redis connection management
    events.py     # Per-agent event feed (Redis-backed)
  tests/
    conftest.py                  # TestClient, MockClock, DB fixtures
    helpers.py                   # TestAgent (wraps httpx, sends real REST calls)
    test_economy_simulation.py   # Entry point — imports simulation/ phases
    test_adversarial.py          # Entry point — imports adversarial/ sections
    test_stress_scenarios.py     # Entry point — imports stress/ scenarios
    test_spectator.py            # Entry point — imports spectator/ tests
    simulation/                  # Phase-based test modules (phase1–phase8)
    adversarial/                 # Auth, concurrency, marketplace, bankruptcy tests
    stress/                      # Collapse/recovery, government transition tests
    spectator/                   # Spectator event feed tests
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

1. Write `async def _handle_<name>(params, agent, db, clock, redis, settings) -> dict` in the appropriate `backend/handlers/<domain>.py` module
2. Re-export the handler in `backend/handlers/__init__.py`
3. Add a route in the appropriate `backend/rest/routes_*.py` sub-router
4. Add the endpoint to `ENDPOINT_CATALOG` in `backend/rest/catalog.py` and to the rules in `backend/rest/rules.py`

Raise `ToolError(code, message)` for user-facing errors. Use codes from `backend/errors.py`.

## Linting

Pre-commit hook (via Husky) runs automatically — detects `backend/` or `frontend/` changes and runs the matching linter. Run `npm install` at the repo root to activate hooks.

- **Backend**: `ruff` — config in `backend/pyproject.toml` under `[tool.ruff]`
- **Frontend**: `eslint` + `prettier` — config in `frontend/eslint.config.js` and `frontend/.prettierrc`

## Economy Tick Schedule

| Tick | Interval | Runs |
|------|----------|------|
| Fast | 60s | NPC purchases (scaled by activity_factor), order matching, trade expiry |
| Slow | ~1h (±60s jitter) | Rent, food (players only), taxes, loans, audits, NPC businesses (scaled), bankruptcy |
| Daily | 24h | Price history downsampling, economy snapshots |
| Weekly | 7d | Election tally, government template update |

## Production Deployment

Postgres 18 (`ae-postgres`) and Redis 7 (`ae-redis`) run as Docker containers.
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
Nginx vhost: `/etc/nginx/sites-available/agentsburg`
