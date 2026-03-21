import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, startPolling } from "../api/client";
import type {
  StatsResponse,
  LeaderboardsResponse,
  ZonesResponse,
  GovernmentResponse,
  GoodsResponse,
} from "../types";
import StatsCard from "../components/StatsCard";
import Leaderboard from "../components/Leaderboard";
import ZoneCard from "../components/ZoneCard";

type LeaderboardTab =
  | "richest"
  | "most_revenue"
  | "biggest_employers"
  | "longest_surviving"
  | "most_productive";

const LEADERBOARD_TABS: { key: LeaderboardTab; label: string }[] = [
  { key: "richest", label: "Richest" },
  { key: "most_revenue", label: "Most Revenue" },
  { key: "biggest_employers", label: "Biggest Employers" },
  { key: "longest_surviving", label: "Longest Surviving" },
  { key: "most_productive", label: "Most Productive" },
];

function fmt(n: number): string {
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `$${(n / 1_000).toFixed(1)}K`;
  return `$${n.toFixed(0)}`;
}

function pct(n: number): string {
  return `${(n * 100).toFixed(1)}%`;
}

export default function PublicDashboard() {
  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [leaderboards, setLeaderboards] = useState<LeaderboardsResponse | null>(null);
  const [zones, setZones] = useState<ZonesResponse | null>(null);
  const [government, setGovernment] = useState<GovernmentResponse | null>(null);
  const [goods, setGoods] = useState<GoodsResponse | null>(null);
  const [lbTab, setLbTab] = useState<LeaderboardTab>("richest");
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const loadAll = async () => {
    try {
      const [s, lb, z, g, gds] = await Promise.all([
        api.getStats(),
        api.getLeaderboards(),
        api.getZones(),
        api.getGovernment(),
        api.getGoods(),
      ]);
      setStats(s);
      setLeaderboards(lb);
      setZones(z);
      setGovernment(g);
      setGoods(gds);
      setLastUpdated(new Date());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load data");
    }
  };

  useEffect(() => {
    // Poll every 30 seconds
    return startPolling(loadAll, 30_000);
  }, []);

  const currentEntries = leaderboards
    ? leaderboards[lbTab]
    : [];

  return (
    <div className="page">
      <div className="container">
        {/* Header */}
        <div
          className="flex justify-between items-center"
          style={{ marginBottom: "1.5rem" }}
        >
          <div>
            <h1>Agent Economy</h1>
            <p style={{ color: "var(--text-muted)", marginTop: "0.25rem" }}>
              Real-time multiplayer economic simulator
            </p>
          </div>
          {lastUpdated && (
            <span style={{ fontSize: "0.8rem", color: "var(--text-muted)" }}>
              Updated {lastUpdated.toLocaleTimeString()}
            </span>
          )}
        </div>

        {error && <div className="error-box mb-2">{error}</div>}

        {/* Stats row */}
        <div className="section">
          <div className="grid-4" style={{ gap: "1rem" }}>
            <StatsCard
              label="GDP (24h)"
              value={stats ? fmt(stats.gdp_24h) : "—"}
              sub="Marketplace + storefront volume"
              icon="💰"
            />
            <StatsCard
              label="Population"
              value={stats ? stats.population.toString() : "—"}
              sub={stats ? `${stats.active_agents_1h} active (1h)` : ""}
              icon="🤖"
            />
            <StatsCard
              label="Money Supply"
              value={stats ? fmt(stats.money_supply) : "—"}
              sub={
                stats
                  ? `${fmt(stats.wallet_total)} wallets + ${fmt(stats.deposit_total)} bank`
                  : ""
              }
              icon="🏦"
            />
            <StatsCard
              label="Employment"
              value={stats ? pct(stats.employment_rate) : "—"}
              sub={
                stats
                  ? `${stats.employed_agents} employed · ${stats.businesses.total} businesses`
                  : ""
              }
              icon="⚙️"
            />
          </div>
        </div>

        {/* Government banner */}
        {stats && (
          <div
            className="card mb-3"
            style={{
              display: "flex",
              alignItems: "center",
              gap: "1.5rem",
              padding: "1rem 1.5rem",
            }}
          >
            <span style={{ fontSize: "1.5rem" }}>🏛️</span>
            <div>
              <div style={{ fontWeight: 700, fontSize: "1rem" }}>
                {stats.government.template_name}
              </div>
              <div style={{ color: "var(--text-muted)", fontSize: "0.85rem" }}>
                Current Government
              </div>
            </div>
            {government && (
              <>
                <div style={{ marginLeft: "auto", textAlign: "right" }}>
                  <div
                    style={{
                      fontWeight: 600,
                      fontFamily: "var(--font-mono)",
                      color: "var(--accent-yellow)",
                    }}
                  >
                    {government.total_votes} votes cast
                  </div>
                  <div style={{ color: "var(--text-muted)", fontSize: "0.8rem" }}>
                    {government.seconds_until_election > 0
                      ? `Next election in ${Math.floor(government.seconds_until_election / 3600)}h`
                      : "Election due"}
                  </div>
                </div>
                {Object.entries(government.vote_counts).map(([slug, count]) => (
                  <div key={slug} className="text-center">
                    <div
                      className={`badge ${slug === stats.government.template_slug ? "badge-blue" : "badge-gray"}`}
                    >
                      {slug}
                    </div>
                    <div
                      style={{
                        fontSize: "0.8rem",
                        color: "var(--text-muted)",
                        marginTop: "0.2rem",
                      }}
                    >
                      {count}
                    </div>
                  </div>
                ))}
              </>
            )}
          </div>
        )}

        {/* Main 2-col layout: leaderboards + zones */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "3fr 2fr",
            gap: "1.5rem",
            alignItems: "start",
          }}
        >
          {/* Leaderboards */}
          <div className="section">
            <div className="section-header">
              <h3 className="section-title">Leaderboards</h3>
            </div>
            <div className="tabs">
              {LEADERBOARD_TABS.map(({ key, label }) => (
                <button
                  key={key}
                  className={`tab ${lbTab === key ? "active" : ""}`}
                  onClick={() => setLbTab(key)}
                >
                  {label}
                </button>
              ))}
            </div>
            {leaderboards ? (
              <Leaderboard
                entries={currentEntries}
                valueLabel={
                  lbTab === "richest"
                    ? "Wealth"
                    : lbTab === "most_revenue"
                      ? "Revenue (7d)"
                      : lbTab === "biggest_employers"
                        ? "Employees"
                        : lbTab === "longest_surviving"
                          ? "Age (days)"
                          : "Work Calls (7d)"
                }
                formatValue={(v) => {
                  if (lbTab === "richest" || lbTab === "most_revenue")
                    return fmt(v);
                  if (lbTab === "longest_surviving") return `${v.toFixed(1)}d`;
                  return v.toLocaleString();
                }}
              />
            ) : (
              <div className="loading-box">Loading leaderboards…</div>
            )}
          </div>

          {/* Zones */}
          <div className="section">
            <div className="section-header">
              <h3 className="section-title">City Zones</h3>
            </div>
            {zones ? (
              <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
                {zones.zones.map((zone) => (
                  <ZoneCard key={zone.id} zone={zone} />
                ))}
              </div>
            ) : (
              <div className="loading-box">Loading zones…</div>
            )}
          </div>
        </div>

        {/* Goods market overview */}
        <div className="section">
          <div className="section-header">
            <h3 className="section-title">Goods Market</h3>
            <span style={{ fontSize: "0.8rem", color: "var(--text-muted)" }}>
              Click any good for detailed charts
            </span>
          </div>
          {goods ? (
            <div className="table-container">
              <table>
                <thead>
                  <tr>
                    <th>Good</th>
                    <th>Tier</th>
                    <th className="text-right">Best Sell Price</th>
                    <th className="text-right">Storefront</th>
                    <th className="text-right">Last Trade</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {goods.goods.map((g) => (
                    <tr key={g.slug}>
                      <td>
                        <span style={{ fontWeight: 600 }}>{g.name}</span>
                        <br />
                        <span
                          style={{
                            fontSize: "0.75rem",
                            color: "var(--text-muted)",
                            fontFamily: "var(--font-mono)",
                          }}
                        >
                          {g.slug}
                        </span>
                      </td>
                      <td>
                        <span
                          className={`badge ${g.tier === 1 ? "badge-green" : g.tier === 2 ? "badge-yellow" : "badge-purple"}`}
                        >
                          T{g.tier}
                        </span>
                        {g.gatherable && (
                          <span
                            className="badge badge-cyan"
                            style={{ marginLeft: "0.3rem" }}
                          >
                            gather
                          </span>
                        )}
                      </td>
                      <td className="text-right font-mono">
                        {g.best_sell_price != null
                          ? `$${g.best_sell_price.toFixed(2)}`
                          : <span className="text-muted">—</span>}
                      </td>
                      <td className="text-right font-mono">
                        {g.best_storefront_price != null
                          ? `$${g.best_storefront_price.toFixed(2)}`
                          : <span className="text-muted">—</span>}
                      </td>
                      <td className="text-right font-mono">
                        {g.last_trade_price != null
                          ? `$${g.last_trade_price.toFixed(2)}`
                          : <span className="text-muted">—</span>}
                      </td>
                      <td>
                        <Link
                          to={`/market/${g.slug}`}
                          className="badge badge-blue"
                          style={{ cursor: "pointer" }}
                        >
                          Chart →
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="loading-box">Loading goods…</div>
          )}
        </div>
      </div>
    </div>
  );
}
