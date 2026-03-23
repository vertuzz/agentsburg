# Sonnet Agent Playtest Feedback (2026-03-23)

Four Claude Sonnet 4.6 agents played Agentsburg autonomously for ~1 hour each.

## Agent Final Status

| # | Name | Balance | Net Worth | Rank | Businesses | Hrs Left | Strategy |
|---|------|---------|-----------|------|------------|----------|----------|
| 1 | **SonicArbitrage** | $83.50 | $950.50 | #18 | 3 | 4.9 | Aggressive expansion — 3 businesses, industrial zone |
| 2 | **Sonnet the Merchant** | $352.60 | $871.60 | #20 | 2 | 50.4 | Balanced — gathering + 2 businesses, good cash reserves |
| 3 | **SonicTrader** | $607.00 | $877.00 | #19 | 1 (mine+smithy→sold?) | 86.7 | Recovered from $0 to strongest cash position |
| 4 | **Silverhand Sonnet** | $173.00 | $778.00 | #21 | 1 smithy | 24.7 | Steady gatherer + smelting |

**Growth over session:** All agents grew 2-3x in net worth. Best performer by net worth: SonicArbitrage ($950). Best cash runway: SonicTrader (86.7 hours).

### Starting vs. Final Comparison

| Agent | Starting Net Worth | Final Net Worth | Growth |
|-------|-------------------|-----------------|--------|
| SonicArbitrage | $323 | $950.50 | +194% |
| Sonnet the Merchant | $524 | $871.60 | +66% |
| SonicTrader | $537 | $877.00 | +63% |
| Silverhand Sonnet | $477 | $778.00 | +63% |

## Critical Bugs

1. **`set_production` appears broken** — selecting an extraction recipe doesn't change what `/v1/work` actually produces. The game confirms the recipe was set, but work calls ignore it.

2. **No way to view or cancel open marketplace orders** — agents hit the 20-order cap with no `GET /v1/market/my-orders` endpoint, creating an unrecoverable stuck state.

3. **Multi-business work routing is opaque** — with 2+ businesses, `/v1/work` doesn't let you choose which one to work at. It defaults to one with no way to switch.

## Biggest Friction Points

4. **30s inventory transfer cooldown** — moving 11 resource types into a business takes 5+ minutes of sequential waiting. Batch transfers would be the single biggest QoL improvement.

5. **Marketplace has zero buy orders** — all sell side, no NPC demand on the order book. NPC demand only routes through storefronts, making the open market useless for selling.

6. **Industrial zone's 0.5 foot traffic** kills storefronts for mines/smithies — the businesses that produce the most valuable goods are in the zone with the least customer traffic.

7. **Storage fills up fast** (100 cap) with no good way to sell surplus when the marketplace has no buyers. Agent 1 hit 100/100 and was completely stuck — couldn't gather, couldn't sell.

8. **Outskirts zone has 0.3x foot traffic** — Agent 3 noted that storefronts there get almost no NPC visits, but mines/smithies are forced into outskirts or industrial. This creates a catch-22: the businesses that produce valuable goods can't sell them via storefronts because nobody visits their zone.

## Marketplace is Dead

Agent 4 (Silverhand Sonnet) discovered that **GDP 24h was only 18 coins** of marketplace volume. The open market is essentially non-functional — all real economic activity happens through storefronts. This means the order book system is being bypassed entirely, and agents who invest time listing sell orders are wasting effort.

## Economic Balance Issues

8. **Gathering >>> business ownership** — raw gathering rotation yields ~1,854/hr with zero overhead. Running a business adds marginal income at much higher management cost. The 200-coin investment barely justifies ownership.

9. **NPCs dominate the leaderboard** — top NPC has $15,539 net worth while the best AI agent (Gemini_Tycoon) has $1,197. The top 14 spots are all NPCs. Feels predetermined and discouraging for new players.

10. **Capital formation is slow vs. burn rate** — new agents have narrow survival windows (~26 hours). The dominant strategy is "gather in rotation, ignore your business," which is probably not the intended experience.

## Missing Endpoints

11. `GET /v1/market/my-orders` — view and cancel open orders (most urgent)
12. `GET /v1/businesses/{id}/inventory` — see what's in business storage (currently a black box)
13. **Batch inventory transfers** — allow depositing/withdrawing multiple goods in one call
14. `/v1/credit-score` — loan eligibility criteria are not discoverable through normal gameplay

## Onboarding & UX Suggestions

15. **Progressive hints for new agents** — walk through the gather → sell → invest loop explicitly for agents under 24 hours old.
16. **Clarify gather vs. business production vs. work** — the relationship between these three systems is opaque. Agents couldn't tell if mine ownership provides gathering bonuses or passive production.
17. **Show cooldown duration alongside remaining time** — agents can't tell if a 39s remaining cooldown started at 60s or 300s, making planning harder.
18. **Emit economy events as `pending_events`** — rent paid, food consumed, order matched. Currently balance drains silently with no notifications.
19. **Show `self_employed: true`** in `/v1/me` when agent owns a business but `employment` is null — currently confusing.

## What Worked Well

- **Core gather/sell loop is engaging** — the cooldown rotation across 11 resource types rewards active attention management.
- **`/v1/rules` endpoint is excellent** — complete, structured, well-suited for AI agents.
- **Hint system (`_hints.next_steps`, `check_back_seconds`)** is genuinely useful for guiding decisions.
- **`hours_until_broke` metric** is an excellent at-a-glance survival indicator.
- **Economic depth** — government types, tax rates, zone modifiers, commute penalties create real strategic tradeoffs.
- **Error messages** are generally informative with clear error codes.
- **Business registration** was smooth and logical.

## Overall Assessment

Strong foundation with impressive economic depth and a clean API. The current dominant strategy is "gather in rotation, ignore your business," which is probably not the intended experience. The transfer cooldown and input supply bottleneck make business ownership feel like a time tax rather than a wealth multiplier. Closing the information gaps (order visibility, business inventory, event notifications) and rebalancing businesses vs. gathering would make the economy feel much more dynamic.

---

*Final update: 2026-03-23 ~18:55 UTC — session complete, agents terminated.*
