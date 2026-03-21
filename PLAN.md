# Agent Economy — Implementation Plan

> Real-time multiplayer economic simulator where AI agents connect via MCP to participate in a virtual city economy.

## Development Philosophy: Simulation-Driven

**Read SPEC.md "Simulation-Driven Development" section first. It is the most important part of the spec.**

There are NO unit tests. The entire quality mechanism is 2-3 large end-to-end simulation tests that run the full economy with dozens of scripted agents. These simulations are built **early** and expanded with every phase — not bolted on at the end.

**The implementing agent's mandate**: When the simulation reveals problems (weird prices, instant bankruptcies, dead markets, exploits), **fix the code and tune the YAML configs autonomously**. Don't ask permission, don't file bugs — investigate, hypothesize, fix, re-run. The configs are hypotheses, not requirements. The simulation loop IS the development process.

Think like an agent that will live in this economy. If something feels broken, it probably is.

---

## Context

Building from scratch based on SPEC.md. Greenfield monorepo with Python backend (FastAPI + SQLAlchemy async + PostgreSQL + Redis) and React frontend. Key design decisions:

- Auth: DB-stored opaque tokens (not JWTs)
- Food/survival: abstract balance deduction
- Storage limits: enforced per agent/business
- Business type bonus: reduced cooldown (single knob)
- Seed money: central bank starts with reserves, NPCs get loans
- Audits: server tracks ALL transactions, tax authority sees only marketplace/storefront subset
- Voting: changeable anytime before weekly tally
- Bankruptcy: instant liquidation to bank at 50% discount, immediate resume with scarred record
- Marketplace: partial fills, standard order book
- Commute: longer work() cooldown
- Gathering: available anywhere

---

## Phase 0: Project Skeleton & Infrastructure

**Goal**: Runnable monorepo with Docker Compose, DB connection, health endpoint.

- [ ] Initialize monorepo structure:
  ```
  backend/
    pyproject.toml
    alembic.ini
    alembic/
    backend/
      __init__.py
      main.py              # FastAPI app factory
      config.py            # YAML loader + Pydantic settings
      database.py          # async SQLAlchemy engine + sessionmaker
      redis.py             # Redis connection
      clock.py             # Clock protocol (RealClock + MockClock)
      models/
        __init__.py
        base.py            # DeclarativeBase, mixins
  frontend/
    package.json
    vite.config.ts
    src/
      main.tsx
      App.tsx
  config/
    goods.yaml
    recipes.yaml
    zones.yaml
    government.yaml
    npc_demand.yaml
    economy.yaml
    bootstrap.yaml
  docker-compose.yaml
  Dockerfile.backend
  Dockerfile.frontend
  ```
- [ ] `docker-compose.yaml` — postgres, redis, backend, frontend services
- [ ] `pyproject.toml` — fastapi, uvicorn, sqlalchemy[asyncio], asyncpg, redis, pyyaml, pydantic, pydantic-settings, httpx, pytest, pytest-asyncio
- [ ] `config.py` — load YAML files into frozen Pydantic models. Single `Settings` accessible via `app.state`
- [ ] `clock.py` — `Clock` protocol with `now() -> datetime`. `RealClock` + `MockClock(advance(seconds))`. All domain code uses Clock, never `datetime.now()`
- [ ] `database.py` — `async_sessionmaker`, `get_db()` FastAPI dependency
- [ ] `main.py` — FastAPI app with lifespan (init DB, load config, connect Redis). `/health` endpoint
- [ ] Alembic setup for async migrations
- [ ] **Verify**: `docker compose up` starts everything, `/health` returns 200

---

## Phase 1: Agent Identity & MCP Protocol

**Goal**: Agents can sign up and authenticate via MCP. First two tools working.

### Database Models

- [ ] `models/agent.py` — Agent table: id, name (unique), action_token (unique, indexed), view_token (unique, indexed), balance (Numeric), zone_id, housing_zone_id, storage_capacity, bankruptcy_count, jail_until, violation_count, created_at, updated_at
- [ ] `models/zone.py` — Zone table: id, slug (unique), name, rent_cost, foot_traffic, demand_multiplier, allowed_business_types (JSON). Seeded from zones.yaml
- [ ] `models/transaction.py` — Transaction table: id, type (enum: wage/rent/food/tax/fine/trade/marketplace/loan_payment/deposit_interest/loan_disbursement/gathering/business_reg/bankruptcy_liquidation), from_agent_id, to_agent_id, amount, metadata (JSON), created_at. Master audit trail.

### Domain: Agents

- [ ] `agents/service.py`:
  - `signup(name)` — generate `secrets.token_urlsafe(32)` tokens, create Agent with balance=0
  - `get_agent_by_action_token(token)` / `get_agent_by_view_token(token)`
  - `get_status(agent)` — full status dict

### MCP Protocol Layer

- [ ] `mcp/protocol.py` — JSON-RPC 2.0 parsing, route `initialize`, `tools/list`, `tools/call`
- [ ] `mcp/auth.py` — extract Bearer token from headers, resolve to Agent. `signup` exempted
- [ ] `mcp/tools.py` — tool registry with name/description/inputSchema. Dispatcher maps name → handler
- [ ] `mcp/router.py` — single `POST /mcp` endpoint, Streamable HTTP (stateless request/response, no SSE)
- [ ] Implement tools: `signup`, `get_status`

### Config Files (stubs)

- [ ] `config/zones.yaml` — 4-5 zones (downtown, suburbs, industrial, waterfront, outskirts)
- [ ] `config/economy.yaml` — survival_cost, base_cooldown, commute_penalty, storage limits, etc.

- [ ] **Verify**: POST JSON-RPC to `/mcp`, sign up agent, get status back

---

## Phase 2: Housing, Gathering, Survival Loop

**Goal**: Agents can rent housing, gather resources. Slow tick deducts survival costs. Bankruptcy works.

### Database Models

- [ ] `models/inventory.py` — InventoryItem: id, owner_type (agent/business), owner_id, good_slug, quantity. Unique on (owner_type, owner_id, good_slug)
- [ ] `models/good.py` — Good: slug (PK), name, tier (1/2/3), storage_size, base_gather_cooldown, base_value. Seeded from goods.yaml

### Domain Logic

- [ ] `agents/housing.py` — `rent_housing(agent, zone_slug)`: check affordability, set housing_zone_id
- [ ] `agents/gathering.py` — `gather(agent, resource_slug)`: check cooldown in Redis (key with TTL), produce 1 unit into inventory, enforce storage limit. Only tier-1 gatherable goods.
- [ ] `agents/inventory.py` — `add_to_inventory()` (check storage limit), `remove_from_inventory()`, `get_inventory()`

### Tick System Foundation

- [ ] `economy/tick.py` — entry point: Redis lock (`SETNX tick:lock`), always run fast tick, check hourly/daily boundaries for slow tick
- [ ] `economy/slow_tick.py` — `process_survival_costs()` (food deduction), `process_rent()`. Flag negative-balance agents.
- [ ] `economy/bankruptcy.py` — `process_bankruptcies()`: liquidate inventory at 50% to bank, cancel all orders/trades/contracts, zero balance, increment bankruptcy_count
- [ ] `economy/cli.py` — `python -m backend.economy.cli` runs one tick cycle (called by cron/Docker)

### MCP Tools

- [ ] `rent_housing(zone)`, `gather(resource)`

### Config

- [ ] `config/goods.yaml` — ~30 goods across 3 tiers. Raw gatherable: wood, stone, berries, clay, iron_ore, herbs, cotton, wheat, fish

### Simulation Infrastructure (built HERE, not later)

Tests go through the **real MCP API** — same HTTP endpoint, same JSON-RPC protocol, same auth. No direct domain calls. If the test works, a real agent doing the same calls will get the same result.

- [ ] `tests/conftest.py`:
  - FastAPI `TestClient` via `httpx.ASGITransport` (in-process, no network, but full middleware/auth/routing stack)
  - Test PostgreSQL database fixture (separate test DB, real async queries)
  - `MockClock` with `advance(seconds)` — injected into app, the ONLY thing that's mocked
  - `run_ticks(clock, n)` — advance clock and trigger tick processing at correct boundaries
  - `assert_money_supply_conserved(db)` — sum all balances + reserves + escrow = expected total
- [ ] `tests/helpers.py` — `TestAgent` class:
  - `TestAgent.signup(client, name)` → sends real `POST /mcp` with JSON-RPC `tools/call` for `signup`, stores action_token
  - `agent.call(tool_name, params)` → sends real `POST /mcp` with Bearer auth, JSON-RPC envelope, returns parsed result
  - This is the ONLY abstraction. Everything under it is the real system: real HTTP, real auth, real protocol parsing, real DB writes.
- [ ] `tests/test_simulation.py` — **first simulation**: 5-10 TestAgents that call `gather`, `rent_housing` via real API. Run ~7 simulated days.
  - Assert: survival costs deducted correctly, gathering cooldowns enforced (get proper error on early retry), storage limits enforced, agents without income go bankrupt, money conservation holds
  - **Tune**: if agents die too fast or survive too easily, adjust `economy.yaml` values (survival_cost, rent, gather cooldown). This is the first calibration cycle.

- [ ] **Verify via simulation**: run it, review output, adjust config. Repeat until the basic survival loop feels right.

---

## Phase 3: Businesses, Production, Employment

**Goal**: Full production chain works. Agents can register businesses, produce goods, hire workers.

### Database Models

- [ ] `models/business.py` — Business: id, owner_id, name, type_slug, zone_id, storage_capacity, is_npc, closed_at. StorefrontPrice: business_id, good_slug, price (unique on business+good). JobPosting: business_id, title, wage_per_work, product_slug, max_workers, is_active. Employment: agent_id, business_id, job_posting_id, wage_per_work, product_slug, hired_at, terminated_at
- [ ] `models/recipe.py` — Recipe: slug, output_good, output_quantity, cooldown_seconds, bonus_business_type, bonus_cooldown_multiplier, inputs (JSON array)

### Domain Logic

- [ ] `businesses/service.py` — `register_business()`, `close_business()`, `configure_production()`, `set_prices()`
- [ ] `businesses/employment.py` — `post_job()`, `apply_job()`, `fire_employee()`, `quit_job()`, `hire_npc_worker()`
- [ ] `businesses/production.py` — `work(agent)`: the core function
  - Routes by context (employed vs self-employed)
  - Check per-agent global cooldown in Redis
  - Verify employer has inputs → deduct inputs → add output to business inventory → pay wage
  - Apply commute penalty (multiply cooldown if housing_zone != business_zone)
  - Apply business type bonus (multiply cooldown by bonus_multiplier if type matches)
  - Reject if jailed

### MCP Tools

- [ ] `register_business(name, type, zone)`, `configure_production(business_id, product, assigned_workers?)`, `set_prices(business_id, product, price)`, `manage_employees(business_id, action, ...)` (multiplexed: post_job/hire_npc/fire/quit_job/close_business), `list_jobs(zone?, type?, min_wage?, page?)`, `apply_job(job_id)`, `work()`

### Config

- [ ] `config/recipes.yaml` — ~20-25 recipes across 3 tiers

### Expand Simulation

- [ ] Add to `test_simulation.py`: 10+ new TestAgents with business strategies — all via real API calls: `register_business`, `configure_production`, `set_prices`, `manage_employees`, `apply_job`, `work`. Gatherer→manufacturer→retailer chain. Run ~14 simulated days.
  - Assert: full production chain works end-to-end via API, wages flow correctly, business type bonus reduces cooldown (verified by checking cooldown error timing), commute penalty applied
  - **Tune**: if production is too slow/fast, recipes produce too much/little, cooldowns feel wrong — adjust `recipes.yaml` and `economy.yaml`. Iterate until supply chains feel alive.

- [ ] **Verify via simulation**: goods should flow through tiers. If any tier bottlenecks or overflows, adjust recipe ratios.

---

## Phase 4: Marketplace & Direct Trading

**Goal**: Order book with partial fills. Direct agent-to-agent trading with escrow.

### Database Models

- [ ] `models/marketplace.py` — MarketOrder: agent_id, good_slug, side (buy/sell), quantity_total, quantity_filled, price, status (open/filled/partially_filled/cancelled). MarketTrade: buy_order_id, sell_order_id, good_slug, quantity, price, executed_at. Trade: proposer_id, target_id, offer_items (JSON), request_items (JSON), offer_money, request_money, status (pending/accepted/rejected/cancelled/expired), escrow_locked, expires_at

### Domain Logic

- [ ] `marketplace/orderbook.py` — `place_order()` (lock inventory/funds, attempt immediate match with price-time priority, partial fills), `cancel_order()`, `browse_orders()`, `get_price_history()`
- [ ] `marketplace/trading.py` — `propose_trade()` (lock in escrow, type=trade NOT visible to tax), `respond_trade()`, `cancel_trade()`, `expire_trades()` (fast tick)

### Fast Tick Addition

- [ ] `economy/fast_tick.py` — `match_pending_orders()`, `expire_trades()`

### MCP Tools

- [ ] `marketplace_order(action, product, quantity, price?, order_id?)`, `marketplace_browse(product?, zone?, page?)`, `trade(action, target_agent?, offer_items?, request_items?, trade_id?, accept?)`

### Expand Simulation

- [ ] Add marketplace agents — all via real `marketplace_order` and `trade` API calls: speculators (buy low/sell high), competing sellers undercutting each other. Direct trade pairs doing off-book deals via `trade(action="propose")` / `trade(action="respond")`.
  - Assert: order book matches correctly with partial fills, prices converge toward equilibrium, escrow locks/returns items correctly, direct trades don't show in marketplace data (verify via `get_economy`)
  - **Tune**: if the market is illiquid or prices are wild, adjust NPC demand curves and initial good supplies

- [ ] **Verify via simulation**: marketplace should show price discovery. If all goods sell at the same price or prices diverge wildly, something is wrong.

---

## Phase 5: Banking & Loans

**Goal**: Central bank with fractional reserve, deposits, credit scoring.

### Database Models

- [ ] `models/banking.py` — BankAccount: agent_id (unique), balance. Loan: agent_id, principal, remaining_balance, interest_rate, installment_amount, installments_remaining, next_payment_at, status. CentralBank: (singleton) reserves, total_loaned

### Domain Logic

- [ ] `banking/service.py` — `deposit()`, `withdraw()`, `calculate_credit()` (score from net_worth, employment, bankruptcy_count, violations, age → max_loan + interest_rate), `take_loan()` (check credit + fractional reserve capacity), `process_loan_payments()` (slow tick), `process_deposit_interest()` (slow tick)

### Bootstrap

- [ ] `economy/bootstrap.py` — `bootstrap_economy()`: seed zones/goods/recipes from YAML, create CentralBank with initial_reserves, create NPC businesses with inventory and storefront prices

### Config

- [ ] `config/bootstrap.yaml` — initial_reserves, NPC business definitions (10-15 across all tiers)

### MCP Tools

- [ ] `bank(action, amount?)` — deposit/withdraw/take_loan/view_balance

### Expand Simulation

- [ ] Add agents via real `bank` API calls: deposit savings, take loans to start businesses, deliberately default on loans. All through `bank(action="deposit")`, `bank(action="take_loan")`, etc.
  - Assert: fractional reserve creates money correctly, credit scoring reflects bankruptcy history (verified by `get_status` showing worse loan terms after bankruptcy), loan defaults trigger bankruptcy, deposit interest accrues
  - **Tune**: if money supply explodes (hyperinflation) or contracts (deflation death spiral), adjust reserve_ratio and interest rates. This is a critical calibration — iterate until money supply grows steadily.

- [ ] **Verify via simulation**: money supply chart should show gradual growth. If it's exponential or flat, the banking parameters are wrong.

---

## Phase 6: Government, Taxes, Crime

**Goal**: Government templates, voting, tax collection, audits, fines, jail.

### Database Models

- [ ] `models/government.py` — GovernmentState: (singleton) current_template_slug, last_election_at. Vote: agent_id (unique), template_slug. Violation: agent_id, type, amount_evaded, fine_amount, jail_until, detected_at. TaxRecord: agent_id, period, marketplace_income, total_actual_income, tax_owed, tax_paid, discrepancy, audited

### Domain Logic

- [ ] `government/service.py` — `get_current_policy()`, `cast_vote()` (upsert, changeable anytime), `tally_election()` (weekly: count eligible votes, apply winner, adjust loan rates)
- [ ] `government/taxes.py` — `collect_taxes()` (sum marketplace txns, apply rate, deduct, create TaxRecord), `run_audits()` (random selection by enforcement prob, compare marketplace vs total income, fine/jail for violations)
- [ ] `government/jail.py` — `is_jailed(agent)`, jailed agents: degraded business operation, can't call strategic tools

### Config

- [ ] `config/government.yaml` — 4 templates (free_market, social_democracy, authoritarian, libertarian) each with: tax_rate, enforcement_probability, interest_rate_modifier, reserve_ratio, licensing_cost_modifier, production_cooldown_modifier, rent_modifier

### MCP Tools

- [ ] `vote(government_type)`, `get_economy(section?, zone?, product?, page?)`

### Expand Simulation

- [ ] Add political agents via real `vote` API calls. Tax evaders use real `trade` (direct trades) to avoid taxable `marketplace_order` calls. Compliant businesses use marketplace only.
  - Assert: elections produce correct winners (verified by `get_economy`), policy changes apply immediately, audits detect evaders, fines and jail work, jailed agents get error responses from strategic tools (`register_business`, `marketplace_order`, etc.)
  - **Tune**: if enforcement is too harsh (everyone jailed) or too lax (crime always pays), adjust enforcement_probability and fine multipliers. Crime should be tempting but risky.

- [ ] **Verify via simulation**: under authoritarian government, economic activity should visibly slow. Under free market, crime should be more profitable but less punished.

---

## Phase 7: NPC Simulation & Storefront

**Goal**: NPC consumers buy from storefronts, NPC businesses respond to market. Economy self-sustains.

### Domain Logic

- [ ] `economy/npc_consumers.py` — `simulate_npc_purchases()` (fast tick): per zone calculate demand per good, weight distribution by price (inverse — cheaper gets more), execute purchases, summary Transaction records
- [ ] `economy/npc_businesses.py` — `simulate_npc_businesses()` (slow tick): auto-produce, restock, profitability check (close if unprofitable), demand gap check (open new if needed), adjust prices
- [ ] NPC worker simulation: auto-produce at reduced efficiency, higher wage

### Config

- [ ] `config/npc_demand.yaml` — per-good base_demand and price_elasticity

### Tick System (Complete)

- [ ] Fast tick: `simulate_npc_purchases()`, `match_pending_orders()`, `expire_trades()`
- [ ] Slow tick: `process_survival_costs()`, `process_rent()`, `collect_taxes()`, `process_loan_payments()`, `process_deposit_interest()`, `run_audits()`, `simulate_npc_businesses()`, `process_bankruptcies()`, `tally_election()` (weekly)

### Expand Simulation → Full Scenarios

This is where the simulation becomes the **complete 3-scenario suite** described in the spec:

- [ ] **Scenario 1: Free Market Boom** — 20+ agents, all strategies, ~30 simulated days. Full economy with production chains, marketplace, banking, free market government. Assert money conservation, price convergence, GDP growth, agent→NPC transition.
- [ ] **Scenario 2: Authoritarian Crackdown** — start free market, vote to authoritarian. Tax evaders, compliant businesses, political agents. Assert elections, enforcement, economic slowdown.
- [ ] **Scenario 3: Collapse & Recovery** — thriving economy → mass defaults → verify NPC fills gaps → new agents survive → recovery curve.

Each scenario outputs a CSV/JSON report with economic metrics. The implementing agent should **review these reports and iterate on both code and config** until the economy behaves believably. This is the most important milestone in the project.

- [ ] **Verify**: all 3 scenarios run, invariants hold, reports look reasonable. If the economy is broken at this point, go back and fix whatever phase is causing it.

---

## Phase 8: Messaging & MCP Polish

**Goal**: All 18 MCP tools complete with response hints and error codes.

### Database Models

- [ ] `models/message.py` — Message: from_agent_id, to_agent_id, text, read (bool), created_at

### MCP Tools

- [ ] `messages(action, to_agent?, text?, page?)` — send/read

### Polish

- [ ] Response hints on all tools: `_hints: {pending_events, check_back_seconds, cooldown_remaining}`
- [ ] Standardized error codes: `INSUFFICIENT_FUNDS`, `COOLDOWN_ACTIVE`, `IN_JAIL`, `NOT_FOUND`, `STORAGE_FULL`, `UNAUTHORIZED`, etc.
- [ ] `tools/list` returns all 18 tools with full JSON Schema inputSchema
- [ ] `initialize` returns server info and capabilities

### Tool Checklist (all 18)

1. `signup` (P1) | 2. `get_status` (P1) | 3. `list_jobs` (P3) | 4. `apply_job` (P3) | 5. `work` (P3) | 6. `register_business` (P3) | 7. `configure_production` (P3) | 8. `set_prices` (P3) | 9. `manage_employees` (P3) | 10. `marketplace_order` (P4) | 11. `marketplace_browse` (P4) | 12. `trade` (P4) | 13. `rent_housing` (P2) | 14. `gather` (P2) | 15. `bank` (P5) | 16. `get_economy` (P6) | 17. `messages` (P8) | 18. `vote` (P6)

---

## Phase 9: REST API & React Dashboard

**Goal**: Public and private dashboards showing live economy.

### REST API

- [ ] `api/router.py` — mounted at `/api/`
- [ ] Public endpoints (no auth):
  - `GET /api/stats` — GDP, population, government, money supply
  - `GET /api/leaderboards` — richest, most revenue, biggest employer, longest surviving
  - `GET /api/market/{good}` — price history, order book depth
  - `GET /api/zones` — zone info with business counts
  - `GET /api/government` — current template, vote counts
- [ ] Private endpoints (view_token query param):
  - `GET /api/agent?token=xxx` — status, balance, inventory
  - `GET /api/agent/transactions?token=xxx&page=N` — transaction history
  - `GET /api/agent/businesses?token=xxx` — business performance
  - `GET /api/agent/messages?token=xxx` — messages

### Frontend

- [ ] `src/api/client.ts` — API client with polling helper
- [ ] Pages: `PublicDashboard.tsx` (stats, leaderboards, price charts, zone grid), `AgentDashboard.tsx` (balance, inventory, transactions, businesses), `MarketDetail.tsx` (order book, price chart)
- [ ] Components: Leaderboard, PriceChart (recharts), TransactionTable, ZoneCard, StatsCard, Navbar
- [ ] Routes: `/` (public), `/market/:good`, `/dashboard?token=xxx`
- [ ] Polling: `setInterval` at 15-30s

- [ ] **Verify**: dashboard shows live data, agent views private stats.

---

## Phase 10: Data Maintenance

**Goal**: Historical data downsampling.

- [ ] `models/aggregate.py` — PriceAggregate (per-good hourly/daily OHLCV), EconomySnapshot (periodic macro stats)
- [ ] `economy/maintenance.py` — `downsample_data()`: minute→hourly after 24h, hourly→daily after 30d. Separate cron (every 6h)

---

## Phase 11: Docker & Deployment Polish

- [ ] `Dockerfile.backend`, `Dockerfile.frontend`
- [ ] `docker-compose.yaml` — full stack including tick-cron service (loop every 60s)
- [ ] `nginx.conf` — serve frontend, proxy `/api/*` and `/mcp` to backend
- [ ] Alembic migration on startup, bootstrap check
- [ ] **Verify**: `docker compose up --build` brings up working system from scratch

---

## Key Reference

### Core Tables

agents, zones, goods, recipes, businesses, storefront_prices, job_postings, employments, inventory_items, market_orders, market_trades, trades, transactions, bank_accounts, loans, central_bank, government_state, votes, violations, tax_records, messages, price_aggregates, economy_snapshots

### Redis Keys

| Key | TTL | Purpose |
|-----|-----|---------|
| `cooldown:gather:{agent_id}` | config | Gathering cooldown |
| `cooldown:work:{agent_id}` | computed | Production cooldown |
| `tick:lock` | 120s | Prevent overlapping ticks |
| `tick:last_hourly` / `daily` / `weekly` | — | Track tick boundaries |

### Package Dependencies

```
config.py, clock.py, database.py  ← foundation
models/                            ← shared
agents/                            ← standalone
businesses/                        ← depends on agents
marketplace/                       ← depends on agents, businesses
banking/                           ← depends on agents
government/                        ← depends on agents, banking
economy/                           ← orchestrator, depends on ALL
mcp/                               ← interface, depends on ALL
api/                               ← interface, depends on ALL
```
