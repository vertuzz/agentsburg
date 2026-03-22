/* ═══════════════════════════════════════════════════════
   API client + TanStack Query hooks
   ═══════════════════════════════════════════════════════ */

import { useQuery } from '@tanstack/react-query';
import type {
  EconomyStats, Leaderboards, AgentSummary, AgentDetail,
  BusinessSummary, BusinessDetail, MarketGood, Good, Zone,
  GovernmentInfo, Transaction, EconomySnapshot, ModelStats,
} from './types';

const BASE = '';  // same origin, proxied by Vite in dev

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`API ${res.status}: ${path}`);
  return res.json();
}

// ── Refresh intervals ──
const FAST = 15_000;   // 15s for live data
const MED  = 30_000;   // 30s for moderate data
const SLOW = 60_000;   // 60s for stable data

// ── Public endpoints ──

export function useStats() {
  return useQuery<EconomyStats>({
    queryKey: ['stats'],
    queryFn: () => get('/api/stats'),
    refetchInterval: FAST,
  });
}

export function useLeaderboards() {
  return useQuery<Leaderboards>({
    queryKey: ['leaderboards'],
    queryFn: () => get('/api/leaderboards'),
    refetchInterval: MED,
  });
}

export function useGoods() {
  return useQuery<{ goods: Good[] }>({
    queryKey: ['goods'],
    queryFn: () => get('/api/goods'),
    refetchInterval: SLOW,
  });
}

export function useZones() {
  return useQuery<{ zones: Zone[] }>({
    queryKey: ['zones'],
    queryFn: () => get('/api/zones'),
    refetchInterval: SLOW,
  });
}

export function useGovernment() {
  return useQuery<GovernmentInfo>({
    queryKey: ['government'],
    queryFn: () => get('/api/government'),
    refetchInterval: MED,
  });
}

export function useMarketGood(good: string) {
  return useQuery<MarketGood>({
    queryKey: ['market', good],
    queryFn: () => get(`/api/market/${good}`),
    refetchInterval: FAST,
    enabled: !!good,
  });
}

// ── New endpoints ──

export function useAgents(page = 1) {
  return useQuery<{ agents: AgentSummary[]; total: number }>({
    queryKey: ['agents', page],
    queryFn: () => get(`/api/agents?page=${page}&page_size=50`),
    refetchInterval: MED,
  });
}

export function useAgent(id: string) {
  return useQuery<AgentDetail>({
    queryKey: ['agent', id],
    queryFn: () => get(`/api/agents/${id}`),
    refetchInterval: MED,
    enabled: !!id,
  });
}

export function useBusinesses(page = 1, zone?: string, type?: string) {
  const params = new URLSearchParams({ page: String(page), page_size: '50' });
  if (zone) params.set('zone', zone);
  if (type) params.set('type', type);
  return useQuery<{ businesses: BusinessSummary[]; total: number }>({
    queryKey: ['businesses', page, zone, type],
    queryFn: () => get(`/api/businesses?${params}`),
    refetchInterval: MED,
  });
}

export function useBusiness(id: string) {
  return useQuery<BusinessDetail>({
    queryKey: ['business', id],
    queryFn: () => get(`/api/businesses/${id}`),
    refetchInterval: MED,
    enabled: !!id,
  });
}

export function useRecentTransactions(limit = 50, type?: string) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (type) params.set('type', type);
  return useQuery<{ transactions: Transaction[] }>({
    queryKey: ['transactions', limit, type],
    queryFn: () => get(`/api/transactions/recent?${params}`),
    refetchInterval: FAST,
  });
}

export function useEconomyHistory() {
  return useQuery<{ snapshots: EconomySnapshot[] }>({
    queryKey: ['economy-history'],
    queryFn: () => get('/api/economy/history'),
    refetchInterval: SLOW,
  });
}

export function useModelStats() {
  return useQuery<{ models: ModelStats[] }>({
    queryKey: ['models'],
    queryFn: () => get('/api/models'),
    refetchInterval: MED,
  });
}
