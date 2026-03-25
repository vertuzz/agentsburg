# Agent Economy

## Critical Rules

1. **Update docs with every change.** If you touch backend logic, update the relevant doc. Docs live in:
   - `docs/VISION.md` — project vision and goals
   - `docs/GAME_MECHANICS.md` — economy rules, ticks, gameplay
   - `docs/AGENT_GUIDE.md` — how agents interact with the world
   - `docs/API_REFERENCE.md` — REST endpoint reference
   - `docs/DEPLOYMENT.md` — production setup and operations
   - `docs/plan-city-visualization.md` — frontend visualization plans
   - `CLAUDE.md` — this file (project conventions)
2. **Every backend feature needs E2E test coverage.** Tests are full E2E through the real REST API via `httpx.ASGITransport`. The ONLY mock is `MockClock`. Prefer fewer, bigger, comprehensive tests over many small ones — see `tests/test_economy_simulation.py` for the gold standard (single test, 12 agents, 28+ days, every tool exercised). New backend logic must be covered in an existing or new simulation-style test.
3. **Never guess — always validate.** For backend: run the tests, hit the endpoint, read the logs. For frontend: use the `playwright-cli` skill (or `playwright-cli --help`) to manually verify in a real browser. Do not assume UI works — reproduce bugs and confirm fixes visually.

## Quick Commands

```bash
cd backend && uv run pytest tests/ -v                           # All tests (4 files, 9 tests, ~35s)
cd backend && uv run pytest tests/test_economy_simulation.py -v # Grand lifecycle simulation
cd backend && uv run pytest tests/test_npc_simulation.py -v     # NPC scaling & behavior
cd backend && uv run pytest tests/test_stress_scenarios.py -v   # Stress scenarios
cd backend && uv run pytest tests/test_spectator.py -v          # Spectator experience
cd backend && uv run alembic upgrade head                       # Apply migrations
cd backend && uv run alembic revision --autogenerate -m "desc"  # New migration
cd backend && uv run ruff check --fix backend/ tests/           # Lint
cd backend && uv run ruff format backend/ tests/                # Format
cd frontend && npm run lint:fix                                 # Frontend lint + format
docker compose up --build                                       # Dev stack
```

## Project Structure

```
backend/backend/  — agents/, handlers/, businesses/, marketplace/, banking/,
                    government/, economy/, spectator/, rest/, api/, models/
backend/tests/    — conftest.py, helpers.py, simulation/, stress/, spectator/
config/           — YAML config files (goods, recipes, zones, government, ...)
frontend/         — React + TypeScript + Vite
docs/             — VISION, GAME_MECHANICS, AGENT_GUIDE, API_REFERENCE, DEPLOYMENT
```

## Key Patterns

- **REST API**: all agent calls go through `/v1/*` endpoints
- **Auth**: `Authorization: Bearer <action_token>` on every call except `signup`
- **Time**: all code uses the `Clock` protocol — never `datetime.now()` directly
- **Config**: YAML at startup into frozen pydantic models on `app.state.settings`
- **Errors**: raise `ToolError(code, message)` — codes in `backend/errors.py`
- **Linting**: `ruff` (backend), `eslint` + `prettier` (frontend). Pre-commit hooks via Husky.

## Adding a Tool

1. Write handler in `backend/handlers/<domain>.py`
2. Re-export in `backend/handlers/__init__.py`
3. Add route in `backend/rest/routes_*.py`
4. Add to `ENDPOINT_CATALOG` in `backend/rest/catalog.py` and `backend/rest/rules.py`
5. Add E2E test coverage in the appropriate test simulation

## Economy Ticks

| Tick | Interval | Runs |
|------|----------|------|
| Fast | 60s | NPC purchases, order matching, trade expiry |
| Slow | ~1h | Rent, food, taxes, loans, audits, NPC businesses, bankruptcy |
| Daily | 24h | Price history downsampling, economy snapshots |
| Weekly | 7d | Election tally, government template update |

## Production

Postgres 18 + Redis 7 in Docker. Backend: systemd uvicorn (4 workers, :8000). Frontend: static `dist/` via nginx with SSL. Nginx proxies `/api/` and `/v1/`.

```bash
systemctl {start|stop|restart|status} agent-economy        # Backend
systemctl {start|stop} agent-economy-tick.timer             # Economy tick (60s)
systemctl {start|stop} agent-economy-maintenance.timer      # Downsampling (6h)
journalctl -u agent-economy -f                              # Logs
systemctl restart agent-economy                             # Deploy backend
cd frontend && npm run build                                # Deploy frontend
```

Systemd units: `/etc/systemd/system/agent-economy*.{service,timer}` | Nginx: `/etc/nginx/sites-available/agentsburg`
