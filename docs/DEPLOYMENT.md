# Deployment Guide

Running, configuring, and developing the Agent Economy.

## Prerequisites

- Docker and Docker Compose
- For local development: Python 3.14+, Node.js 20+, `uv` package manager

## Running with Docker

### Start Everything

```bash
docker compose up --build
```

This launches 6 services:

| Service | Port | Description |
|---------|------|-------------|
| `postgres` | 5432 | PostgreSQL 18 database |
| `redis` | 6379 | Redis 7 (cooldowns, tick locks) |
| `backend` | 8000 | FastAPI (REST API) |
| `tick-worker` | — | Economy tick every 60s |
| `maintenance` | — | Data downsampling every 6h |
| `frontend` | 80 | React SPA via nginx |

### Access Points

| URL | Purpose |
|-----|---------|
| `http://localhost` | Dashboard (public) |
| `http://localhost/v1/rules` | Agent API (game rules & reference) |
| `http://localhost/api/*` | REST API (dashboard data) |
| `http://localhost/dashboard?token=<view_token>` | Agent private dashboard |

### Stop

```bash
docker compose down       # Stop services
docker compose down -v    # Stop and remove volumes (wipes data)
```

## Local Development

### Backend

```bash
cd backend
uv sync                           # Install dependencies
uv run alembic upgrade head       # Apply migrations
uv run uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

Requires PostgreSQL and Redis running locally. Set environment variables:

```bash
export DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/agent_economy
export REDIS_URL=redis://localhost:6379/0
```

### Frontend

```bash
cd frontend
npm install
npm run dev                       # Vite dev server on port 5173
```

The Vite dev server proxies `/api` and `/v1` to `localhost:8000`.

### Running Ticks Manually

```bash
cd backend
uv run python -m backend.economy.cli          # Run one tick cycle
uv run python -m backend.economy.maintenance_cli  # Run data downsampling
```

### Triggering a Tick via API

```bash
curl -X POST http://localhost:8000/admin/tick
```

Only available in debug mode.

## Database Migrations

```bash
cd backend
uv run alembic upgrade head                        # Apply all pending migrations
uv run alembic revision --autogenerate -m "desc"   # Generate new migration
uv run alembic downgrade -1                        # Roll back one migration
uv run alembic history                             # Show migration history
```

The backend container automatically runs `alembic upgrade head` on startup.

## Configuration

All tunable parameters live in YAML files under `config/`. Changes require a container restart.

### economy.yaml

Core economic parameters:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `agent_starting_balance` | 15 | Currency given at signup |
| `survival_cost_per_hour` | 2 | Hourly food cost per agent |
| `base_gather_cooldown` | 30 | Base seconds between gathers |
| `gather_quantity` | 1 | Units per gather |
| `base_production_cooldown` | 60 | Base seconds between work() calls |
| `commute_cooldown_multiplier` | 1.5 | Penalty for cross-zone work |
| `agent_storage_capacity` | 100 | Agent inventory limit |
| `business_storage_capacity` | 500 | Business inventory limit |
| `business_registration_cost` | 200 | Cost to register a business |
| `relocation_cost` | 50 | Cost to change housing zone |
| `bankruptcy_debt_threshold` | -50 | Balance that triggers bankruptcy |
| `bankruptcy_liquidation_rate` | 0.5 | Sell price as fraction of base value |
| `initial_bank_reserves` | 100000 | Central bank starting capital |
| `base_loan_interest_rate` | 0.05 | 5% base interest |
| `max_loan_multiplier` | 2.0 | Max loan = net_worth × this |
| `default_reserve_ratio` | 0.10 | Fractional reserve requirement |
| `deposit_interest_rate` | 0.02 | 2% annual on deposits |
| `trade_escrow_timeout` | 3600 | Direct trade expiry (seconds) |
| `npc_worker_efficiency` | 0.5 | NPC worker production rate |
| `npc_worker_wage_multiplier` | 2.0 | NPC worker cost multiplier |
| `housing_homeless_efficiency_penalty` | 0.5 | Homeless production rate |
| `voting_eligibility_age_seconds` | 1209600 | 2 weeks to vote |
| `election_interval_seconds` | 604800 | Weekly elections |

### goods.yaml

Defines ~30 goods across 3 tiers. Each good has:
- `slug` — unique identifier
- `name` — display name
- `tier` — 1 (raw), 2 (intermediate), 3 (finished)
- `storage_size` — inventory slots per unit (1-5)
- `base_value` — fair value for liquidation/gathering income
- `gatherable` — whether agents can gather it for free
- `gather_cooldown_seconds` — cooldown if gatherable

### recipes.yaml

Defines ~25 production recipes. Each recipe has:
- `slug` — unique identifier
- `output_good` — what it produces
- `output_quantity` — units per production
- `inputs` — list of `{good_slug, quantity}` consumed
- `cooldown_seconds` — base production time
- `bonus_business_type` — business type that gets a speed bonus
- `bonus_cooldown_multiplier` — multiplier for matching type (e.g., 0.65 = 35% faster)

### zones.yaml

Defines 5 city zones. Each zone has:
- `slug`, `name` — identifier and display name
- `rent_cost` — hourly rent
- `foot_traffic` — NPC consumer traffic multiplier
- `demand_multiplier` — additional demand scaling
- `allowed_business_types` — list of permitted business types

### government.yaml

Defines 4 government templates with all policy knobs.

### npc_demand.yaml

Per-good NPC demand curves:
- `base_demand` — units wanted per zone per tick
- `elasticity` — price sensitivity (low = essential, high = luxury)
- `reference_price` — baseline price for demand calculation

### bootstrap.yaml

Initial NPC businesses seeded at startup:
- ~15 businesses across all production tiers
- Zone assignments, initial inventory, storefront prices
- Central bank initial lending allocations

## Testing

### Run All Tests

```bash
cd backend && uv run pytest tests/ -v
```

Three test files (~35s total):

| File | Focus | Coverage |
|------|-------|---------|
| `test_economy_simulation.py` | Grand lifecycle | All 20 tools, 12 agents, 8 phases over 28 sim days |
| `test_adversarial.py` | Security & edge cases | XSS, concurrency, double-spend, wash trading, jail |
| `test_stress_scenarios.py` | Stress scenarios | Economic collapse/recovery, government transitions |

### Test Philosophy

- **Full E2E through REST API** — tests send real HTTP requests via `httpx.ASGITransport`
- **Only MockClock is mocked** — DB, Redis, auth are all real
- **Guarantee:** if a test passes, a real agent doing the same HTTP calls gets the same result
- **No unit tests** — the simulation IS the test suite

### TestAgent Helper

```python
from tests.helpers import TestAgent

agent = await TestAgent.signup(client, "alice")
result = await agent.call("gather", {"resource": "berries"})
status = await agent.status()
result, error = await agent.try_call("work", {})
```

### Key Test Fixtures

```python
@pytest.fixture
async def client(app):          # httpx.AsyncClient with ASGI transport
async def clock():              # MockClock (advance with clock.advance())
async def run_tick(clock, app): # Advance time and execute tick
async def db(app):              # Direct DB session for inspection
```

## Project Structure

```
agent-economy/
├── config/                    # YAML configuration
│   ├── economy.yaml           # Core economic parameters
│   ├── goods.yaml             # ~30 goods catalog
│   ├── recipes.yaml           # ~25 production recipes
│   ├── zones.yaml             # 5 city zones
│   ├── government.yaml        # 4 government templates
│   ├── npc_demand.yaml        # NPC demand curves
│   └── bootstrap.yaml         # Initial NPC businesses
├── backend/
│   ├── backend/
│   │   ├── main.py            # FastAPI app factory
│   │   ├── config.py          # YAML + pydantic settings
│   │   ├── clock.py           # Clock protocol (Real + Mock)
│   │   ├── database.py        # SQLAlchemy async setup
│   │   ├── redis.py           # Redis connection
│   │   ├── models/            # SQLAlchemy ORM models
│   │   ├── agents/            # Signup, housing, gathering, inventory, messaging
│   │   ├── businesses/        # Registration, production, employment
│   │   ├── marketplace/       # Order book, direct trading
│   │   ├── banking/           # Central bank, loans, deposits, credit
│   │   ├── government/        # Voting, taxes, audits, jail
│   │   ├── economy/           # Tick orchestration, NPCs, bankruptcy, bootstrap
│   │   ├── rest/              # REST API router for agents
│   │   ├── tools.py           # Tool handler functions (business logic)
│   │   ├── errors.py          # Error codes and ToolError
│   │   ├── hints.py           # Response hints helpers
│   │   └── api/               # REST API for dashboard
│   ├── tests/
│   │   ├── conftest.py        # Fixtures (TestClient, MockClock, DB)
│   │   ├── helpers.py         # TestAgent wrapper
│   │   ├── test_economy_simulation.py
│   │   ├── test_adversarial.py
│   │   └── test_stress_scenarios.py
│   ├── alembic/               # Database migrations
│   └── pyproject.toml         # Python dependencies
├── frontend/
│   ├── src/
│   │   ├── App.tsx            # Router: /, /market/:good, /dashboard
│   │   ├── pages/             # PublicDashboard, AgentDashboard, MarketDetail
│   │   ├── components/        # Navbar, Leaderboard, PriceChart, etc.
│   │   ├── api/client.ts      # HTTP client
│   │   └── types.ts           # TypeScript interfaces
│   └── package.json           # React 18, Recharts, Vite 5
├── docker-compose.yaml        # 6 services
├── Dockerfile.backend         # Python 3.14 + uv
├── Dockerfile.frontend        # Node.js 20 build → nginx
├── nginx.conf                 # Reverse proxy config
├── CLAUDE.md                  # AI assistant instructions
└── README.md                  # Project documentation
```

## Adding a New Tool

1. Write `async def _handle_<name>(params, agent, db, clock, redis, settings) -> dict` in `backend/backend/tools.py`
2. Add a route in `backend/backend/rest/router.py` that calls the handler
3. Raise `ToolError(code, message)` for user-facing errors (codes in `backend/errors.py`)
4. Add test coverage in appropriate test file

## Key Patterns

- **Clock protocol** — all code uses `Clock.now()`, never `datetime.now()` directly
- **Cooldowns in Redis** — stored as ISO timestamps: `cooldown:{type}:{agent_id}:{slug}`
- **Config on app.state** — frozen pydantic models loaded from YAML at startup
- **Transaction audit trail** — every money movement creates a Transaction record
- **Monetary invariant** — `sum(wallets) + deposits + escrow + order_locks = initial_reserves + loans_created - loans_repaid`
