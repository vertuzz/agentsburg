import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { api, startPolling } from "../api/client";
import type { MarketResponse } from "../types";
import PriceChart from "../components/PriceChart";

export default function MarketDetail() {
  const { good } = useParams<{ good: string }>();
  const [market, setMarket] = useState<MarketResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const loadMarket = async () => {
    if (!good) return;
    try {
      const data = await api.getMarket(good);
      setMarket(data);
      setLastUpdated(new Date());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load market data");
    }
  };

  useEffect(() => {
    setMarket(null);
    setError(null);
    return startPolling(loadMarket, 15_000);
  }, [good]);

  if (error) {
    return (
      <div className="page">
        <div className="container">
          <div className="mb-2">
            <Link to="/">← Back to Dashboard</Link>
          </div>
          <div className="error-box">{error}</div>
        </div>
      </div>
    );
  }

  const stats = market?.stats_24h;
  const orderBook = market?.order_book;

  return (
    <div className="page">
      <div className="container">
        <div className="flex items-center gap-2 mb-2">
          <Link to="/" style={{ color: "var(--text-muted)", fontSize: "0.9rem" }}>
            ← Dashboard
          </Link>
          <span style={{ color: "var(--border)" }}>/</span>
          <span style={{ color: "var(--text-secondary)" }}>Markets</span>
          <span style={{ color: "var(--border)" }}>/</span>
          <span style={{ fontWeight: 600 }}>{good}</span>
        </div>

        {/* Header */}
        <div
          className="flex justify-between items-center"
          style={{ marginBottom: "1.5rem" }}
        >
          <div>
            <h2>{market?.good.name ?? good}</h2>
            <div style={{ color: "var(--text-muted)", fontSize: "0.85rem" }}>
              {market?.good.slug} · Tier {market?.good.tier ?? "—"}
              {market?.good.gatherable && (
                <span
                  className="badge badge-green"
                  style={{ marginLeft: "0.5rem" }}
                >
                  Gatherable
                </span>
              )}
            </div>
          </div>
          {lastUpdated && (
            <span style={{ fontSize: "0.8rem", color: "var(--text-muted)" }}>
              Updated {lastUpdated.toLocaleTimeString()}
            </span>
          )}
        </div>

        {/* 24h stats row */}
        <div className="grid-4 mb-3">
          <div className="stats-card">
            <div className="stat-label">24h Volume (units)</div>
            <div className="stat-value">
              {stats ? stats.volume_qty.toLocaleString() : "—"}
            </div>
          </div>
          <div className="stats-card">
            <div className="stat-label">24h High</div>
            <div className="stat-value" style={{ color: "var(--accent-green)" }}>
              {stats?.high != null ? `$${stats.high.toFixed(2)}` : "—"}
            </div>
          </div>
          <div className="stats-card">
            <div className="stat-label">24h Low</div>
            <div className="stat-value" style={{ color: "var(--accent-red)" }}>
              {stats?.low != null ? `$${stats.low.toFixed(2)}` : "—"}
            </div>
          </div>
          <div className="stats-card">
            <div className="stat-label">24h Average</div>
            <div className="stat-value">
              {stats?.average != null ? `$${stats.average.toFixed(2)}` : "—"}
            </div>
            <div className="stat-sub">
              ${stats?.volume_value.toFixed(2) ?? "0"} total value
            </div>
          </div>
        </div>

        {/* Price chart */}
        <div className="mb-3">
          {market ? (
            <PriceChart
              data={market.price_history}
              title="Price History (last 100 trades)"
              height={260}
            />
          ) : (
            <div className="chart-wrapper">
              <div className="loading-box">Loading chart…</div>
            </div>
          )}
        </div>

        {/* Order book + recent trades */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gap: "1.5rem",
          }}
        >
          {/* Order book */}
          <div>
            <div className="section-header">
              <h3 className="section-title">Order Book</h3>
              {orderBook && (
                <div style={{ fontSize: "0.82rem", color: "var(--text-muted)" }}>
                  Best bid:{" "}
                  <span style={{ color: "var(--accent-green)" }}>
                    {orderBook.best_buy != null
                      ? `$${orderBook.best_buy.toFixed(2)}`
                      : "—"}
                  </span>
                  {" · "}
                  Best ask:{" "}
                  <span style={{ color: "var(--accent-red)" }}>
                    {orderBook.best_sell != null
                      ? `$${orderBook.best_sell.toFixed(2)}`
                      : "—"}
                  </span>
                </div>
              )}
            </div>
            {orderBook ? (
              <div className="card" style={{ padding: "0" }}>
                {/* Buy side */}
                <div style={{ padding: "0.75rem 1rem" }}>
                  <div
                    className="order-book-header buy"
                    style={{ marginBottom: "0.5rem" }}
                  >
                    Bids (Buy Orders)
                  </div>
                  {orderBook.buy.length === 0 ? (
                    <div
                      style={{
                        color: "var(--text-muted)",
                        fontSize: "0.85rem",
                        padding: "0.5rem 0",
                      }}
                    >
                      No buy orders
                    </div>
                  ) : (
                    <div>
                      <div
                        className="order-row"
                        style={{
                          fontSize: "0.72rem",
                          color: "var(--text-muted)",
                          marginBottom: "0.25rem",
                        }}
                      >
                        <span>Price</span>
                        <span className="text-right">Quantity</span>
                      </div>
                      {orderBook.buy.map((level, i) => (
                        <div key={i} className="order-row">
                          <span style={{ color: "var(--accent-green)" }}>
                            ${level.price.toFixed(2)}
                          </span>
                          <span className="text-right">{level.quantity}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                <div
                  style={{ borderTop: "1px solid var(--border)", height: 1 }}
                />

                {/* Sell side */}
                <div style={{ padding: "0.75rem 1rem" }}>
                  <div
                    className="order-book-header sell"
                    style={{ marginBottom: "0.5rem" }}
                  >
                    Asks (Sell Orders)
                  </div>
                  {orderBook.sell.length === 0 ? (
                    <div
                      style={{
                        color: "var(--text-muted)",
                        fontSize: "0.85rem",
                        padding: "0.5rem 0",
                      }}
                    >
                      No sell orders
                    </div>
                  ) : (
                    <div>
                      <div
                        className="order-row"
                        style={{
                          fontSize: "0.72rem",
                          color: "var(--text-muted)",
                          marginBottom: "0.25rem",
                        }}
                      >
                        <span>Price</span>
                        <span className="text-right">Quantity</span>
                      </div>
                      {orderBook.sell.map((level, i) => (
                        <div key={i} className="order-row">
                          <span style={{ color: "var(--accent-red)" }}>
                            ${level.price.toFixed(2)}
                          </span>
                          <span className="text-right">{level.quantity}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <div className="loading-box">Loading order book…</div>
            )}
          </div>

          {/* Recent trades */}
          <div>
            <div className="section-header">
              <h3 className="section-title">Recent Trades</h3>
            </div>
            {market ? (
              <div className="table-container">
                <table>
                  <thead>
                    <tr>
                      <th className="text-right">Price</th>
                      <th className="text-right">Qty</th>
                      <th>Time</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...market.price_history].reverse().slice(0, 30).map(
                      (trade, i) => (
                        <tr key={i}>
                          <td
                            className="text-right font-mono"
                            style={{ color: "var(--accent-blue)" }}
                          >
                            ${trade.price.toFixed(2)}
                          </td>
                          <td className="text-right font-mono">
                            {trade.quantity}
                          </td>
                          <td
                            style={{
                              fontSize: "0.8rem",
                              color: "var(--text-muted)",
                            }}
                          >
                            {new Date(trade.executed_at).toLocaleTimeString([], {
                              hour: "2-digit",
                              minute: "2-digit",
                              second: "2-digit",
                            })}
                          </td>
                        </tr>
                      ),
                    )}
                    {market.price_history.length === 0 && (
                      <tr>
                        <td
                          colSpan={3}
                          className="text-center"
                          style={{ color: "var(--text-muted)", padding: "2rem" }}
                        >
                          No trades yet
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="loading-box">Loading trades…</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
