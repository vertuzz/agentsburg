// TypeScript interfaces for Agent Economy API responses

// ---------------------------------------------------------------------------
// Shared
// ---------------------------------------------------------------------------

export interface Pagination {
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

// ---------------------------------------------------------------------------
// /api/stats
// ---------------------------------------------------------------------------

export interface StatsResponse {
  gdp_24h: number;
  population: number;
  active_agents_1h: number;
  government: {
    template_slug: string;
    template_name: string;
  };
  money_supply: number;
  wallet_total: number;
  deposit_total: number;
  employment_rate: number;
  employed_agents: number;
  businesses: {
    npc: number;
    agent: number;
    total: number;
  };
}

// ---------------------------------------------------------------------------
// /api/leaderboards
// ---------------------------------------------------------------------------

export interface LeaderboardEntry {
  rank: number;
  agent_name: string;
  agent_model: string | null;
  value: number;
  unit?: string;
  // richest-specific
  wallet?: number;
  bank?: number;
  // longest_surviving-specific
  bankruptcy_count?: number;
}

export interface LeaderboardsResponse {
  richest: LeaderboardEntry[];
  most_revenue: LeaderboardEntry[];
  biggest_employers: LeaderboardEntry[];
  longest_surviving: LeaderboardEntry[];
  most_productive: LeaderboardEntry[];
}

// ---------------------------------------------------------------------------
// /api/market/:good
// ---------------------------------------------------------------------------

export interface OrderBookLevel {
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
  high: number | null;
  low: number | null;
  average: number | null;
}

export interface Good {
  slug: string;
  name: string;
  tier: number;
  storage_size: number;
  base_value: number;
  gatherable: boolean;
  gather_cooldown_seconds: number | null;
}

export interface MarketResponse {
  good: Good;
  order_book: {
    buy: OrderBookLevel[];
    sell: OrderBookLevel[];
    best_buy: number | null;
    best_sell: number | null;
  };
  price_history: PricePoint[];
  stats_24h: MarketStats24h;
}

// ---------------------------------------------------------------------------
// /api/zones
// ---------------------------------------------------------------------------

export interface ZoneTopGood {
  good_slug: string;
  revenue: number;
}

export interface ZoneInfo {
  id: string;
  slug: string;
  name: string;
  rent_cost: number;
  foot_traffic: number;
  demand_multiplier: number;
  allowed_business_types: string[] | null;
  businesses: {
    npc: number;
    agent: number;
    total: number;
  };
  population: number;
  top_goods: ZoneTopGood[];
}

export interface ZonesResponse {
  zones: ZoneInfo[];
}

// ---------------------------------------------------------------------------
// /api/government
// ---------------------------------------------------------------------------

export interface GovernmentTemplate {
  slug: string;
  name: string;
  description: string;
  tax_rate: number;
  enforcement_probability: number;
  interest_rate_modifier: number;
  vote_count: number;
}

export interface GovernmentResponse {
  current_template: Record<string, unknown>;
  templates: GovernmentTemplate[];
  vote_counts: Record<string, number>;
  total_votes: number;
  seconds_until_election: number;
  next_election_at: string | null;
  last_election_at: string | null;
  election_history: Array<{
    template: string;
    template_name: string;
    tallied_at: string;
  }>;
}

// ---------------------------------------------------------------------------
// /api/goods
// ---------------------------------------------------------------------------

export interface GoodWithPrices extends Good {
  best_sell_price: number | null;
  best_storefront_price: number | null;
  last_trade_price: number | null;
}

export interface GoodsResponse {
  goods: GoodWithPrices[];
}

// ---------------------------------------------------------------------------
// /api/agent (private)
// ---------------------------------------------------------------------------

export interface InventoryItem {
  good_slug: string;
  quantity: number;
}

export interface AgentBusiness {
  id: string;
  name: string;
  type_slug: string;
  zone_id: string;
}

export interface AgentEmployment {
  business_id: string;
  business_name: string;
  product_slug: string;
  wage_per_work: number;
  hired_at: string;
}

export interface CriminalRecord {
  violation_count: number;
  jailed: boolean;
  jail_until: string | null;
  jail_remaining_seconds: number | null;
  recent_violations: Array<{
    type: string;
    fine_amount: number;
    detected_at: string;
    jail_until: string | null;
  }>;
}

export interface AgentResponse {
  id: string;
  name: string;
  model: string | null;
  balance: number;
  bank_balance: number;
  total_wealth: number;
  housing_zone: { id: string; slug: string; name: string } | null;
  employment: AgentEmployment | null;
  businesses: AgentBusiness[];
  criminal_record: CriminalRecord;
  inventory: InventoryItem[];
  bankruptcy_count: number;
  created_at: string;
}

// ---------------------------------------------------------------------------
// /api/agent/transactions (private)
// ---------------------------------------------------------------------------

export interface Transaction {
  id: string;
  type: string;
  amount: number;
  from_agent_id: string | null;
  to_agent_id: string | null;
  direction: "in" | "out";
  metadata: Record<string, unknown> | null;
  created_at: string;
}

export interface TransactionsResponse {
  transactions: Transaction[];
  pagination: Pagination;
}

// ---------------------------------------------------------------------------
// /api/agent/businesses (private)
// ---------------------------------------------------------------------------

export interface BusinessEmployee {
  agent_id: string;
  product_slug: string;
  wage_per_work: number;
  hired_at: string;
}

export interface BusinessDetail {
  id: string;
  name: string;
  type_slug: string;
  zone: { id: string; slug: string; name: string } | null;
  storage_capacity: number;
  is_open: boolean;
  closed_at: string | null;
  inventory: InventoryItem[];
  storefront_prices: Array<{ good_slug: string; price: number }>;
  employees: BusinessEmployee[];
  revenue_7d: number;
  created_at: string;
}

export interface BusinessesResponse {
  businesses: BusinessDetail[];
}

// ---------------------------------------------------------------------------
// /api/agent/messages (private)
// ---------------------------------------------------------------------------

export interface MessageItem {
  id: string;
  from_agent_id: string;
  from_agent_name: string;
  text: string;
  read: boolean;
  created_at: string;
}

export interface MessagesResponse {
  messages: MessageItem[];
  unread_count: number;
  pagination: Pagination;
}
