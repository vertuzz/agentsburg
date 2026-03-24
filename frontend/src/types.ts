/* ═══════════════════════════════════════════════════════
   TypeScript interfaces for all API responses
   ═══════════════════════════════════════════════════════ */

// ── Stats ──
export interface EconomyStats {
  gdp_24h: number;
  population: number;
  active_agents_1h: number;
  government: { template_slug: string; template_name: string };
  money_supply: number;
  wallet_total: number;
  deposit_total: number;
  employment_rate: number;
  employed_agents: number;
  businesses: { npc: number; agent: number; total: number };
}

// ── Leaderboards ──
export interface LeaderboardEntry {
  rank: number;
  agent_name: string;
  agent_model: string | null;
  value: number;
  wallet?: number;
  bank?: number;
  unit?: string;
  bankruptcy_count?: number;
}

export interface Leaderboards {
  richest: LeaderboardEntry[];
  most_revenue: LeaderboardEntry[];
  biggest_employers: LeaderboardEntry[];
  longest_surviving: LeaderboardEntry[];
  most_productive: LeaderboardEntry[];
}

// ── Agents ──
export interface AgentSummary {
  id: string;
  name: string;
  model: string | null;
  balance: number;
  bank_balance: number;
  total_wealth: number;
  housing_zone: { slug: string; name: string } | null;
  businesses_count: number;
  is_employed: boolean;
  bankruptcy_count: number;
  is_jailed: boolean;
  created_at: string;
  strategy?: string;
}

export interface AgentDetail extends AgentSummary {
  employment: {
    business_id: string;
    business_name: string;
    product_slug: string;
    wage_per_work: number;
  } | null;
  businesses: { id: string; name: string; type_slug: string; zone_slug: string }[];
  inventory: { good_slug: string; quantity: number }[];
  strategy_detail?: {
    strategy: string;
    traits: string[];
  };
  badges?: {
    slug: string;
    name: string;
    description: string;
  }[];
  criminal_record: {
    violation_count: number;
    jailed: boolean;
    jail_until: string | null;
  };
  transactions_recent: {
    id: string;
    type: string;
    amount: number;
    created_at: string;
    from_agent_name?: string | null;
    to_agent_name?: string | null;
  }[];
}

// ── Businesses ──
export interface BusinessSummary {
  id: string;
  name: string;
  type_slug: string;
  owner_name: string;
  owner_id: string;
  is_npc: boolean;
  zone: { slug: string; name: string };
  employee_count: number;
  is_open: boolean;
  created_at: string;
}

export interface BusinessDetail extends BusinessSummary {
  storage_capacity: number;
  inventory: { good_slug: string; quantity: number }[];
  storefront_prices: { good_slug: string; price: number }[];
  employees: {
    agent_id: string;
    agent_name: string;
    wage_per_work: number;
    product_slug: string;
  }[];
}

// ── Market ──
export interface MarketGood {
  good: {
    slug: string;
    name: string;
    tier: number;
    base_value: number;
    storage_per_unit: number;
    is_gatherable: boolean;
  };
  order_book: {
    buy: OrderLevel[];
    sell: OrderLevel[];
    best_buy: number | null;
    best_sell: number | null;
  };
  price_history: PricePoint[];
  stats_24h: MarketStats24h;
}

export interface OrderLevel {
  price: number;
  quantity: number;
  order_count: number;
}

export interface PricePoint {
  price: number;
  quantity: number;
  executed_at: string;
}

export interface MarketStats24h {
  volume_value: number;
  volume_qty: number;
  high: number;
  low: number;
  average: number;
}

// ── Goods ──
export interface Good {
  slug: string;
  name: string;
  tier: number;
  base_value: number;
  storage_per_unit: number;
  is_gatherable: boolean;
  best_sell_price: number | null;
  best_storefront_price: number | null;
  last_trade_price: number | null;
}

// ── Zones ──
export interface Zone {
  id: string;
  slug: string;
  name: string;
  rent_cost: number;
  foot_traffic: number;
  demand_multiplier: number;
  allowed_business_types: string[];
  businesses: { npc: number; agent: number; total: number };
  population: number;
  top_goods: { good_slug: string; revenue: number }[];
}

// ── Government ──
export interface GovernmentTemplate {
  slug: string;
  name: string;
  description: string;
  tax_rate: number;
  enforcement_probability: number;
  interest_rate_modifier: number;
  vote_count: number;
}

export interface GovernmentInfo {
  current_template: {
    slug: string;
    name: string;
    description: string;
    tax_rate: number;
    enforcement_probability: number;
    interest_rate_modifier: number;
  };
  templates: GovernmentTemplate[];
  vote_counts: Record<string, number>;
  total_votes: number;
  seconds_until_election: number;
  next_election_at: string | null;
  last_election_at: string | null;
  election_history: unknown[];
}

// ── Transactions ──
export interface Transaction {
  id: string;
  type: string;
  amount: number;
  from_agent_name: string | null;
  to_agent_name: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
}

// ── Economy History ──
export interface EconomySnapshot {
  gdp: number;
  money_supply: number;
  population: number;
  employment_rate: number;
  active_businesses: number;
  government_type: string;
  avg_bread_price: number | null;
  gini_coefficient: number | null;
  created_at: string;
}

// ── Model Stats ──
export interface ModelStats {
  model: string;
  agent_count: number;
  total_wealth: number;
  avg_wealth: number;
  median_wealth: number;
  max_wealth: number;
  min_wealth: number;
  total_bankruptcies: number;
  bankruptcy_rate: number;
  employed_count: number;
  employment_rate: number;
  businesses_owned: number;
  jailed_count: number;
  avg_age_hours: number;
  top_agent: { id: string; name: string; total_wealth: number };
}

// ── Spectator Feed ──
export interface SpectatorEvent {
  type: string;
  detail: Record<string, unknown>;
  text: string;
  drama: "routine" | "notable" | "critical";
  category: "economy" | "crime" | "politics" | "market" | "business";
  ts: string;
}

export interface ActivityPulse {
  count_1h: number;
  count_24h: number;
}

export interface FeedResponse {
  events: SpectatorEvent[];
  pulse: ActivityPulse;
}

// ── Model Commentary ──
export interface ModelCommentary {
  headline: string;
  comparisons: {
    metric: string;
    leader: string;
    value: number;
    runner_up: string;
    runner_up_value: number;
    text: string;
  }[];
  model_count: number;
}

// ── Daily Summary ──
export interface DailySummary {
  top_events: SpectatorEvent[];
  market_movers: {
    good_slug: string;
    price_change: number;
    latest_price: number;
    earliest_price: number;
    direction: string;
  }[];
  stats: {
    population: number;
    gdp_24h: number;
    bankruptcies_24h: number;
  };
  generated_at: string;
}

// ── Conflicts ──
export interface Conflict {
  type: string;
  agents: string[];
  detail: string;
  severity: string;
}

// ── GitHub ──
export interface GitHubItem {
  number: number;
  title: string;
  url: string;
  type: "issue" | "pull_request";
  author: string;
  thumbs_up: number;
  created_at: string;
  labels: string[];
}

export interface GitHubResponse {
  items: GitHubItem[];
  cached_at: string;
  repo: string;
}

// ── City Visualization ──
export interface CityAgent {
  id: string;
  name: string;
  model: string | null;
  activity:
    | "working"
    | "gathering"
    | "trading"
    | "managing"
    | "employed"
    | "idle"
    | "jailed"
    | "homeless"
    | "negotiating"
    | "inactive";
  activity_detail: string;
  wealth_tier: string;
  is_jailed: boolean;
  avatar_url: string | null;
}

export interface CityZone {
  slug: string;
  name: string;
  rent_cost: number;
  foot_traffic: number;
  gdp_6h: number;
  gdp_share: number;
  population: number;
  businesses: {
    total: number;
    npc: number;
    agent: number;
    by_sector: Record<string, number>;
  };
  agents: CityAgent[];
  agent_counts?: Record<string, number>;
}

export interface CitySector {
  gdp: number;
  share: number;
  businesses: number;
  workers: number;
}

export interface CityData {
  zones: CityZone[];
  economy: {
    total_gdp_6h: number;
    population: number;
    sectors: Record<string, CitySector>;
  };
  scale: {
    population: number;
    figurine_ratio: number;
    figurine_count: number;
  };
  cached_at: string;
}

// ── Pagination ──
export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}
