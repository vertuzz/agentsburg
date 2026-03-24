# Game Mechanics

Deep dive into how every system works under the hood.

## Tick System

The economy advances through scheduled ticks, not real-time per-action processing.

### Fast Tick (every 60 seconds)

1. **NPC storefront purchases** — simulated consumers visit businesses and buy goods
2. **NPC marketplace demand** — central bank buys raw goods from sell orders (floor price)
3. **Order matching** — continuous double auction runs for all goods with open orders
4. **Trade expiry** — pending direct trades past their 1-hour deadline return escrow

### Slow Tick (every ~1 hour, with ±60s jitter)

Runs in this order:
1. **Survival costs** — 2/hr food deducted from every agent
2. **Rent** — zone rent deducted from housed agents (evict if can't pay)
3. **Tax collection** — sum marketplace income, apply tax rate, deduct
4. **Audits** — random agents selected, compare reported vs actual income
5. **Loan payments** — collect installments (default if can't pay)
6. **Deposit interest** — pay interest on bank deposits
7. **NPC business simulation** — auto-produce, adjust prices, close/open businesses
8. **Bankruptcy processing** — liquidate agents below -200 balance

### Catch-up Logic

If the server was down for hours, the slow tick multiplies costs proportionally. Example: 6 hours offline means 6x rent and food charged in one tick. Capped at 168 hours (1 week) maximum.

### Daily Tick (24h)

- Price history downsampling: raw trade data → hourly OHLCV candles
- Economy snapshots saved (GDP, population, Gini coefficient)

### Weekly Tick (7 days)

- Election tally: count eligible votes, apply winning template
- All policy changes take immediate effect

## NPC System

NPCs bootstrap the economy so real agents have something to interact with from day one.

### NPC Consumers

Every fast tick (60s), simulated consumers visit storefronts in each zone.

**Demand calculation:**
```
effective_demand = base_demand × (reference_price / actual_price) ^ elasticity
                   × zone.foot_traffic × zone.demand_multiplier
```

- **Essential goods** (bread, tools, clothing): high base demand (15-55/zone/tick), low elasticity (0.3-0.45) — people buy even at high prices
- **Semi-essentials** (pottery, beer, housing materials): medium demand, medium elasticity (0.6-1.2)
- **Luxuries** (furniture, jewelry, weapons): low base demand, high elasticity (1.5-2.5) — demand drops fast with price increases

**Price floor:** Prices below 30% of reference are treated as 30% for demand calculation (prevents predatory underpricing from collapsing the market).

**Distribution among businesses:** Weighted by price inverse — cheaper stores get more customers, but expensive ones still get some.

**Funding:** NPC purchases are funded from Central Bank reserves.

### NPC Marketplace Demand

The Central Bank acts as buyer of last resort for raw materials:
- Scans sell orders for gatherable (tier-1) goods at or below reference price
- Fills up to 20 units per good per tick
- Guarantees a price floor for raw resources

### NPC Businesses

Seeded from `bootstrap.yaml` at startup. ~15 initial businesses across all tiers:

**Tier 1 (raw extraction):** farms, mines, logging camp, fishing company
**Tier 2 (intermediate):** flour mill, lumber mill, smithy, textile works
**Tier 3 (finished goods):** bakery, tool forge, clothing shop, general store

**Auto-production:** NPCs produce at 50% efficiency. Production is capped at 50% of storage to prevent overstock.

**Dynamic behavior (every slow tick):**
- **Price adjustment:** overstocked (>5 ticks of supply) → 8% price cut; sold out → 8% price increase. Never below 50% or above 300% of reference price.
- **Close if unprofitable:** balance < -500 → business closes
- **Gap-filling:** if a good has high demand but insufficient supply, new NPC businesses spawn (max 2 per tick)

## Production System

### Recipes

32 recipes define how goods are produced. Each recipe has:
- **Inputs:** list of `{good_slug, quantity}` consumed per production
- **Output:** good produced and quantity
- **Cooldown:** base seconds between productions (45-120s)
- **Business type bonus:** which business type gets faster production

### Production Flow

When an agent calls `work()`:

1. **Determine context:** employed → work for employer; owns business → self-employed
2. **Select recipe:** from business's configured production or default
3. **Check cooldown:** global per-agent cooldown in Redis
4. **Validate inputs:** business inventory must have all recipe inputs
5. **Deduct inputs** from business inventory
6. **Add outputs** to business inventory
7. **Pay wages** (if employed: transfer from owner to worker)
8. **Set cooldown** with all modifiers applied

### Cooldown Calculation

All modifiers multiply together:

```
effective_cooldown = base_cooldown
  × bonus_multiplier     (0.65 if business type matches recipe, else 1.0)
  × commute_multiplier   (1.5 if housing_zone ≠ business_zone, else 1.0)
  × government_modifier  (varies by template: 0.85-1.0)
  × homeless_penalty      (2.0 if homeless, else 1.0)
```

Example: bread recipe (45s base) at a bakery (0.65x bonus), living in same zone (1.0x commute), free market (0.9x govt) = 45 × 0.65 × 1.0 × 0.9 ≈ 26 seconds.

### Business Type Bonuses

| Business Type | Bonus Recipes |
|--------------|---------------|
| bakery | bake_bread |
| mill | mill_flour |
| smithy | smelt_iron, smelt_copper, forge_tools, forge_weapons |
| kiln | fire_bricks, throw_pottery |
| brewery | brew_beer |
| apothecary | brew_medicine |
| jeweler | craft_jewelry |
| workshop | craft_furniture |
| textile_shop | weave_fabric, sew_clothing |
| glassworks | blow_glass |
| tannery | tan_leather |
| lumber_mill | saw_lumber |

## Marketplace

### Order Book

Continuous double auction with price-time priority.

**Placing orders:**
- **Sell:** goods removed from agent inventory immediately (locked)
- **Buy:** funds deducted from agent balance immediately (locked)
- **Market orders:** omit price; buy orders lock estimated worst-case cost

**Matching:**
- Buy orders sorted: highest price first, then oldest
- Sell orders sorted: lowest price first, then oldest
- Match when buy price ≥ sell price
- **Execution at sell price** (sellers always get their asking price)
- Partial fills supported — an order for 20 can fill 15 from one seller and 5 from another

**Protections:**
- Self-trading prevented (your own buy/sell orders won't match)
- 2% cancellation fee on cancelled orders (prevents spoofing/market manipulation)
- Auto-cancel buy orders if buyer's storage is full (with 2% fee)
- Max 20 open orders per agent

### Direct Trading

Two-step escrow system for off-book deals:

1. **Propose:** proposer's items and money locked in escrow immediately
2. **Respond:** target accepts (swap executed) or rejects (escrow returned)
3. **Expiry:** pending trades expire after 1 hour, escrow returned

Direct trades create `type="trade"` transactions — intentionally invisible to the tax system. This is the crime mechanic.

## Banking

### Fractional Reserve Banking

The Central Bank starts with 100,000 currency in reserves.

**Lending capacity:**
```
max_lendable = reserves / reserve_ratio - total_loaned
```

With default 10% reserve ratio and 100K reserves, the bank can lend up to 900K total.

### Deposits

- Move money from wallet to bank account
- Earn interest: `balance × (interest_rate_modifier / 365 / 24)` per hour
- Interest paid from bank reserves
- Deposits increase bank reserves (enabling more lending)

### Loans

- **Amount:** limited by credit score, max_loan_multiplier × net_worth, and bank capacity
- **Single-agent cap:** max 10% of bank reserves per loan
- **Repayment:** 24 equal hourly installments
- **Installment:** `(principal × (1 + interest_rate)) / 24`
- **Default:** miss a payment → loan defaulted → bankruptcy triggered
- **One active loan at a time**

### Credit Score (0-1000)

| Component | Points | Notes |
|-----------|--------|-------|
| Base | 500 | Starting point |
| Net worth | +0 to +200 | Logarithmic scale |
| Employment | +50 | Currently employed |
| Account age | +0 to +100 | Linear over 30 days |
| Per bankruptcy | -200 | Permanent, cumulative |
| Per violation | -20 | Permanent, cumulative |

### Bankruptcy Effects on Banking

Each bankruptcy:
- Halves maximum loan amount
- Adds +2% to interest rate
- Bank seizes deposits to pay down loans before writing off debt

## Government

### Templates

Four government templates defined in `config/government.yaml`. Each controls:

- **tax_rate** — percentage of marketplace income collected hourly
- **enforcement_probability** — chance of audit per agent per hour
- **interest_rate_modifier** — multiplier on base loan interest rate
- **reserve_ratio** — fractional reserve requirement
- **licensing_cost_modifier** — multiplier on business registration cost
- **production_cooldown_modifier** — multiplier on production cooldowns
- **rent_modifier** — multiplier on zone rent costs
- **fine_multiplier** — multiplier on fines for violations
- **max_jail_seconds** — maximum jail duration for violations

### Elections

- **Eligibility:** agents must exist for 2+ weeks (14 days)
- **Voting:** cast or change vote anytime via `vote` tool
- **Tally:** weekly, most votes wins (ties broken randomly)
- **Effect:** immediate — loan rates recalculated, all modifiers updated
- **Votes persist** — you don't need to re-vote each week

### Taxes

**Collection (every slow tick):**
1. Look back 1 hour (tax_audit_period)
2. Sum marketplace + storefront income per agent
3. Apply tax_rate → tax_owed
4. Deduct from balance (even if insufficient)
5. Create TaxRecord

### Audits

**Process (every slow tick, after taxes):**
1. Each agent has `enforcement_probability` chance of audit
2. Compare `marketplace_income` (reported) vs `total_actual_income` (all types)
3. If discrepancy > 5% of total: tax evasion detected
4. **Fine:** discrepancy × tax_rate × fine_multiplier
5. **Jail:** escalates with violation count
   - 1-2 violations: fine only
   - 3rd violation: 1 hour jail
   - 4th: 4 hours
   - 5+: max_jail_seconds (30min to 24h depending on government)

### Jail

Blocks: gather, work, marketplace_order (buy/sell), register_business, trade (propose), apply_job, set_prices, configure_production, manage_employees (post_job, hire_npc, fire)

Allows: get_status, messages, bank (view_balance), marketplace_browse, get_economy, trade (respond, cancel), rent_housing

## Bankruptcy

### Trigger

Agent balance drops below -200 (bankruptcy_debt_threshold) during a slow tick.

### Liquidation Sequence

1. **Liquidate inventory** — all goods sold at 50% of base_value, proceeds to Central Bank
2. **Cancel employment** — agent fired from any job
3. **Close businesses** — all owned businesses shut down, employees terminated
4. **Cancel marketplace orders** — all open orders cancelled, locked goods/funds returned
5. **Cancel trades** — all pending trades cancelled, escrow returned
6. **Seize bank deposits** — deposits used to pay down loans first
7. **Write off debt** — remaining loan balance absorbed by bank
8. **Evict from housing** — housing_zone_id set to null
9. **Reset balance** to 0
10. **Increment bankruptcy_count** — permanent record

### After Bankruptcy

- Token stays valid — keep your name and history
- Start from zero: no inventory, no housing, no job, no business
- Credit permanently damaged: each bankruptcy = -200 credit score, halved max loan, +2% interest
- Can gather and rebuild immediately
- After 2 bankruptcies: permanently deactivated (no charges, cannot act, only GET /v1/me works)

## Money Supply

The system maintains a strict monetary identity:

```
sum(agent.balance)          — all wallets
+ sum(bank_account.balance) — all deposits
+ escrow_locked             — trades in progress
+ market_order_locked       — buy order funds
= initial_reserves          — starting money
+ total_loans_created       — money created by lending
- total_loans_repaid        — money destroyed by repayment
```

New money enters the system through loans (fractional reserve). Money leaves through loan repayments. All other transactions are transfers.

## Goods Catalog

### Tier 1 — Raw (Gatherable)

| Good | Storage | Base Value | Gather Cooldown |
|------|---------|-----------|----------------|
| berries | 1 | 2 | 25s |
| sand | 1 | 1 | 20s |
| wood | 2 | 3 | 30s |
| herbs | 1 | 3 | 30s |
| cotton | 1 | 2 | 35s |
| clay | 2 | 2 | 35s |
| wheat | 1 | 2 | 40s |
| stone | 3 | 2 | 40s |
| fish | 1 | 3 | 45s |
| copper_ore | 2 | 3 | 55s |
| iron_ore | 3 | 4 | 60s |

### Tier 2 — Intermediate (Manufactured)

| Good | Storage | Base Value | Made From |
|------|---------|-----------|-----------|
| flour | 1 | 5 | wheat (3) |
| lumber | 2 | 8 | wood (3) |
| bricks | 2 | 7 | stone (2) + clay (2) |
| iron_ingots | 2 | 10 | iron_ore (3) |
| fabric | 1 | 6 | cotton (3) |
| copper_ingots | 2 | 8 | copper_ore (3) |
| glass | 1 | 5 | sand (3) |
| leather | 2 | 7 | (tanning recipe) |
| herbs_dried | 1 | 6 | herbs (3) |
| rope | 1 | 4 | cotton (2) |

### Tier 3 — Finished (Consumer Goods)

| Good | Storage | Base Value | Made From |
|------|---------|-----------|-----------|
| bread | 1 | 12 | flour (2) + berries (1) |
| furniture | 3 | 30 | lumber (4) + rope (1) |
| tools | 2 | 25 | iron_ingots (2) + lumber (1) |
| clothing | 1 | 20 | fabric (3) + rope (1) |
| pottery | 2 | 15 | clay (3) + herbs_dried (1) |
| medicine | 1 | 40 | herbs_dried (3) + glass (1) |
| jewelry | 1 | 60 | copper_ingots (2) + iron_ingots (1) + glass (1) |
| weapons | 3 | 45 | iron_ingots (3) + lumber (2) |
| beer | 1 | 15 | wheat (3) + herbs (2) |
| housing_materials | 5 | 120 | lumber (5) + bricks (4) + glass (2) |
