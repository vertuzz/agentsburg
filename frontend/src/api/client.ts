/**
 * API client for Agent Economy backend.
 *
 * Provides a typed fetch wrapper, base URL configuration,
 * and a polling helper using setInterval.
 */

// Base URL: empty string means same-origin (proxied by Vite in dev)
const BASE_URL = "";

// ---------------------------------------------------------------------------
// Core fetch wrapper
// ---------------------------------------------------------------------------

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(`API Error ${status}: ${detail}`);
    this.name = "ApiError";
  }
}

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const url = `${BASE_URL}/api${path}`;
  const response = await fetch(url, {
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
    ...options,
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail ?? detail;
    } catch {
      // ignore parse error
    }
    throw new ApiError(response.status, detail);
  }

  return response.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Typed API methods
// ---------------------------------------------------------------------------

import type {
  StatsResponse,
  LeaderboardsResponse,
  MarketResponse,
  ZonesResponse,
  GovernmentResponse,
  GoodsResponse,
  AgentResponse,
  TransactionsResponse,
  BusinessesResponse,
  MessagesResponse,
} from "../types";

export const api = {
  // --- Public ---

  getStats(): Promise<StatsResponse> {
    return apiFetch<StatsResponse>("/stats");
  },

  getLeaderboards(): Promise<LeaderboardsResponse> {
    return apiFetch<LeaderboardsResponse>("/leaderboards");
  },

  getMarket(good: string): Promise<MarketResponse> {
    return apiFetch<MarketResponse>(`/market/${encodeURIComponent(good)}`);
  },

  getZones(): Promise<ZonesResponse> {
    return apiFetch<ZonesResponse>("/zones");
  },

  getGovernment(): Promise<GovernmentResponse> {
    return apiFetch<GovernmentResponse>("/government");
  },

  getGoods(): Promise<GoodsResponse> {
    return apiFetch<GoodsResponse>("/goods");
  },

  // --- Private (require view_token) ---

  getAgent(token: string): Promise<AgentResponse> {
    return apiFetch<AgentResponse>(`/agent?token=${encodeURIComponent(token)}`);
  },

  getAgentTransactions(
    token: string,
    page = 1,
    pageSize = 25,
  ): Promise<TransactionsResponse> {
    return apiFetch<TransactionsResponse>(
      `/agent/transactions?token=${encodeURIComponent(token)}&page=${page}&page_size=${pageSize}`,
    );
  },

  getAgentBusinesses(token: string): Promise<BusinessesResponse> {
    return apiFetch<BusinessesResponse>(
      `/agent/businesses?token=${encodeURIComponent(token)}`,
    );
  },

  getAgentMessages(
    token: string,
    page = 1,
    pageSize = 25,
  ): Promise<MessagesResponse> {
    return apiFetch<MessagesResponse>(
      `/agent/messages?token=${encodeURIComponent(token)}&page=${page}&page_size=${pageSize}`,
    );
  },
};

// ---------------------------------------------------------------------------
// Polling helper
// ---------------------------------------------------------------------------

/**
 * Start polling a callback function at a fixed interval.
 * Calls the callback immediately on start, then every `intervalMs` milliseconds.
 *
 * Returns a cleanup function that stops the polling (for use in useEffect).
 *
 * @example
 * useEffect(() => {
 *   return startPolling(() => loadData(), 30_000);
 * }, []);
 */
export function startPolling(
  callback: () => void | Promise<void>,
  intervalMs: number,
): () => void {
  // Call immediately
  void callback();

  const id = setInterval(() => {
    void callback();
  }, intervalMs);

  return () => clearInterval(id);
}
