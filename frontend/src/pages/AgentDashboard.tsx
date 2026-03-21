import { useEffect, useState } from "react";
import { useSearchParams, Link } from "react-router-dom";
import { api, startPolling, ApiError } from "../api/client";
import type {
  AgentResponse,
  TransactionsResponse,
  BusinessesResponse,
  MessagesResponse,
} from "../types";
import TransactionTable from "../components/TransactionTable";

type DashboardTab = "overview" | "transactions" | "businesses" | "messages";

const TABS: { key: DashboardTab; label: string }[] = [
  { key: "overview", label: "Overview" },
  { key: "transactions", label: "Transactions" },
  { key: "businesses", label: "Businesses" },
  { key: "messages", label: "Messages" },
];

function fmt(n: number): string {
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `$${(n / 1_000).toFixed(2)}K`;
  return `$${n.toFixed(2)}`;
}

export default function AgentDashboard() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token") ?? "";

  const [agent, setAgent] = useState<AgentResponse | null>(null);
  const [transactions, setTransactions] = useState<TransactionsResponse | null>(null);
  const [businesses, setBusinesses] = useState<BusinessesResponse | null>(null);
  const [messages, setMessages] = useState<MessagesResponse | null>(null);
  const [tab, setTab] = useState<DashboardTab>("overview");
  const [txPage, setTxPage] = useState(1);
  const [msgPage, setMsgPage] = useState(1);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  // No token — show instructions
  if (!token) {
    return (
      <div className="page">
        <div className="container">
          <div
            className="card"
            style={{ maxWidth: 480, margin: "4rem auto", textAlign: "center" }}
          >
            <h2 style={{ marginBottom: "1rem" }}>Agent Dashboard</h2>
            <p style={{ color: "var(--text-muted)", marginBottom: "1.5rem" }}>
              Add your view token to the URL to access your private dashboard.
            </p>
            <code
              style={{
                display: "block",
                background: "var(--bg-secondary)",
                padding: "0.75rem 1rem",
                borderRadius: "var(--radius-sm)",
                color: "var(--accent-blue)",
                fontSize: "0.9rem",
              }}
            >
              /dashboard?token=YOUR_VIEW_TOKEN
            </code>
            <div style={{ marginTop: "1.5rem" }}>
              <Link to="/">← Back to Public Dashboard</Link>
            </div>
          </div>
        </div>
      </div>
    );
  }

  const loadAgent = async () => {
    try {
      const data = await api.getAgent(token);
      setAgent(data);
      setLastUpdated(new Date());
      setError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError("Invalid view token. Please check your URL.");
      } else {
        setError(err instanceof Error ? err.message : "Failed to load agent data");
      }
    }
  };

  const loadTransactions = async (page = txPage) => {
    try {
      const data = await api.getAgentTransactions(token, page);
      setTransactions(data);
    } catch {
      // silently fail — agent load already handles errors
    }
  };

  const loadBusinesses = async () => {
    try {
      const data = await api.getAgentBusinesses(token);
      setBusinesses(data);
    } catch {
      // ignore
    }
  };

  const loadMessages = async (page = msgPage) => {
    try {
      const data = await api.getAgentMessages(token, page);
      setMessages(data);
    } catch {
      // ignore
    }
  };

  useEffect(() => {
    // Poll agent data every 15 seconds
    return startPolling(loadAgent, 15_000);
  }, [token]);

  useEffect(() => {
    // Load all secondary data on mount
    loadTransactions(1);
    loadBusinesses();
    loadMessages(1);
  }, [token]);

  useEffect(() => {
    loadTransactions(txPage);
  }, [txPage]);

  useEffect(() => {
    loadMessages(msgPage);
  }, [msgPage]);

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

  if (!agent) {
    return (
      <div className="page">
        <div className="container">
          <div className="loading-box">Loading agent data…</div>
        </div>
      </div>
    );
  }

  const now = new Date();
  const createdAt = new Date(agent.created_at);
  const ageDays = Math.floor((now.getTime() - createdAt.getTime()) / 86400000);

  return (
    <div className="page">
      <div className="container">
        {/* Header */}
        <div
          className="flex justify-between items-center"
          style={{ marginBottom: "1.5rem" }}
        >
          <div>
            <div className="flex items-center gap-2">
              <h2>{agent.name}</h2>
              {agent.criminal_record.jailed && (
                <span className="badge badge-red">⛓ Jailed</span>
              )}
              {agent.bankruptcy_count > 0 && (
                <span className="badge badge-yellow">
                  {agent.bankruptcy_count}x bankrupt
                </span>
              )}
            </div>
            {agent.model && (
              <div
                style={{
                  color: "var(--text-muted)",
                  fontSize: "0.85rem",
                  marginTop: "0.2rem",
                }}
              >
                Powered by{" "}
                <span className="badge badge-purple">{agent.model}</span>
              </div>
            )}
            <div
              style={{
                color: "var(--text-muted)",
                fontSize: "0.8rem",
                marginTop: "0.3rem",
              }}
            >
              Age: {ageDays}d · Created{" "}
              {createdAt.toLocaleDateString()}
            </div>
          </div>
          {lastUpdated && (
            <span style={{ fontSize: "0.8rem", color: "var(--text-muted)" }}>
              Updated {lastUpdated.toLocaleTimeString()}
            </span>
          )}
        </div>

        {/* Balance row */}
        <div className="grid-4 mb-3">
          <div className="stats-card">
            <div className="stat-label">Wallet Balance</div>
            <div
              className="stat-value"
              style={{
                color:
                  agent.balance < 0
                    ? "var(--accent-red)"
                    : "var(--accent-green)",
              }}
            >
              {fmt(agent.balance)}
            </div>
          </div>
          <div className="stats-card">
            <div className="stat-label">Bank Deposit</div>
            <div className="stat-value">{fmt(agent.bank_balance)}</div>
          </div>
          <div className="stats-card">
            <div className="stat-label">Total Wealth</div>
            <div className="stat-value">{fmt(agent.total_wealth)}</div>
            <div className="stat-sub">wallet + bank</div>
          </div>
          <div className="stats-card">
            <div className="stat-label">Housing</div>
            <div
              className="stat-value"
              style={{
                fontSize: "1.2rem",
                color: agent.housing_zone
                  ? "var(--text-primary)"
                  : "var(--accent-red)",
              }}
            >
              {agent.housing_zone ? agent.housing_zone.name : "Homeless"}
            </div>
            {agent.housing_zone && (
              <div className="stat-sub">/{agent.housing_zone.slug}</div>
            )}
          </div>
        </div>

        {/* Tabs */}
        <div className="tabs">
          {TABS.map(({ key, label }) => (
            <button
              key={key}
              className={`tab ${tab === key ? "active" : ""}`}
              onClick={() => setTab(key)}
            >
              {label}
              {key === "messages" && messages && messages.unread_count > 0 && (
                <span
                  className="badge badge-red"
                  style={{ marginLeft: "0.4rem", padding: "0.1rem 0.4rem" }}
                >
                  {messages.unread_count}
                </span>
              )}
            </button>
          ))}
        </div>

        {/* Overview tab */}
        {tab === "overview" && (
          <div>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr",
                gap: "1.5rem",
              }}
            >
              {/* Employment */}
              <div className="card">
                <div className="card-header">
                  <span className="card-title">Employment</span>
                </div>
                {agent.employment ? (
                  <div>
                    <div
                      style={{ fontWeight: 600, marginBottom: "0.5rem" }}
                    >
                      {agent.employment.business_name}
                    </div>
                    <div style={{ fontSize: "0.85rem", color: "var(--text-muted)" }}>
                      Producing:{" "}
                      <span className="badge badge-blue">
                        {agent.employment.product_slug}
                      </span>
                    </div>
                    <div
                      style={{
                        fontSize: "0.85rem",
                        color: "var(--text-secondary)",
                        marginTop: "0.4rem",
                      }}
                    >
                      Wage: <strong>{fmt(agent.employment.wage_per_work)}</strong>{" "}
                      per work()
                    </div>
                    <div
                      style={{
                        fontSize: "0.75rem",
                        color: "var(--text-muted)",
                        marginTop: "0.3rem",
                      }}
                    >
                      Since{" "}
                      {new Date(agent.employment.hired_at).toLocaleDateString()}
                    </div>
                  </div>
                ) : (
                  <div
                    style={{ color: "var(--text-muted)", fontSize: "0.9rem" }}
                  >
                    Not employed
                  </div>
                )}
              </div>

              {/* Criminal record */}
              <div className="card">
                <div className="card-header">
                  <span className="card-title">Criminal Record</span>
                  <span
                    className={`badge ${agent.criminal_record.violation_count > 0 ? "badge-red" : "badge-green"}`}
                  >
                    {agent.criminal_record.violation_count} violations
                  </span>
                </div>
                {agent.criminal_record.jailed ? (
                  <div style={{ color: "var(--accent-red)" }}>
                    <div style={{ fontWeight: 700, marginBottom: "0.5rem" }}>
                      Currently jailed
                    </div>
                    {agent.criminal_record.jail_remaining_seconds != null && (
                      <div style={{ fontSize: "0.85rem" }}>
                        Released in{" "}
                        {Math.ceil(
                          agent.criminal_record.jail_remaining_seconds / 3600,
                        )}
                        h
                      </div>
                    )}
                  </div>
                ) : agent.criminal_record.violation_count === 0 ? (
                  <div style={{ color: "var(--accent-green)", fontSize: "0.9rem" }}>
                    Clean record
                  </div>
                ) : (
                  <div>
                    {agent.criminal_record.recent_violations
                      .slice(0, 3)
                      .map((v, i) => (
                        <div
                          key={i}
                          style={{
                            fontSize: "0.85rem",
                            marginBottom: "0.3rem",
                            padding: "0.3rem 0",
                            borderBottom: "1px solid var(--border)",
                          }}
                        >
                          <span className="badge badge-red">{v.type}</span>
                          <span
                            style={{
                              marginLeft: "0.5rem",
                              color: "var(--accent-red)",
                            }}
                          >
                            Fine: {fmt(v.fine_amount)}
                          </span>
                        </div>
                      ))}
                  </div>
                )}
              </div>
            </div>

            {/* Inventory */}
            <div className="card mt-2">
              <div className="card-header">
                <span className="card-title">Inventory</span>
                <span style={{ fontSize: "0.8rem", color: "var(--text-muted)" }}>
                  {agent.inventory.length} item types
                </span>
              </div>
              {agent.inventory.length === 0 ? (
                <div
                  style={{ color: "var(--text-muted)", fontSize: "0.9rem" }}
                >
                  Empty inventory
                </div>
              ) : (
                <div
                  style={{
                    display: "flex",
                    flexWrap: "wrap",
                    gap: "0.5rem",
                  }}
                >
                  {agent.inventory.map((item) => (
                    <div
                      key={item.good_slug}
                      className="badge badge-blue"
                      style={{
                        fontSize: "0.85rem",
                        padding: "0.3rem 0.7rem",
                        display: "flex",
                        gap: "0.4rem",
                        alignItems: "center",
                      }}
                    >
                      <Link
                        to={`/market/${item.good_slug}`}
                        style={{ color: "inherit", fontWeight: 600 }}
                      >
                        {item.good_slug}
                      </Link>
                      <span
                        style={{
                          background: "rgba(255,255,255,0.15)",
                          borderRadius: "10px",
                          padding: "0 0.4rem",
                        }}
                      >
                        ×{item.quantity}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Owned businesses summary */}
            {agent.businesses.length > 0 && (
              <div className="card mt-2">
                <div className="card-header">
                  <span className="card-title">My Businesses</span>
                  <button
                    className="badge badge-blue"
                    style={{ cursor: "pointer", border: "none" }}
                    onClick={() => setTab("businesses")}
                  >
                    View details →
                  </button>
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
                  {agent.businesses.map((biz) => (
                    <span key={biz.id} className="badge badge-purple">
                      {biz.name} ({biz.type_slug})
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* Transactions tab */}
        {tab === "transactions" && (
          <div className="card" style={{ padding: 0 }}>
            <div className="card-header" style={{ padding: "1rem 1.25rem" }}>
              <span className="card-title">Transaction History</span>
              {transactions && (
                <span style={{ fontSize: "0.8rem", color: "var(--text-muted)" }}>
                  {transactions.pagination.total} total
                </span>
              )}
            </div>
            <div style={{ padding: "0 1.25rem 1.25rem" }}>
              <TransactionTable
                transactions={transactions?.transactions ?? []}
                pagination={
                  transactions?.pagination ?? {
                    page: 1,
                    page_size: 25,
                    total: 0,
                    total_pages: 1,
                  }
                }
                onPageChange={(p) => setTxPage(p)}
                loading={!transactions}
              />
            </div>
          </div>
        )}

        {/* Businesses tab */}
        {tab === "businesses" && (
          <div>
            {!businesses ? (
              <div className="loading-box">Loading businesses…</div>
            ) : businesses.businesses.length === 0 ? (
              <div className="card">
                <div className="empty-box">No businesses owned</div>
              </div>
            ) : (
              businesses.businesses.map((biz) => (
                <div key={biz.id} className="card mb-2">
                  <div className="card-header">
                    <div>
                      <div style={{ fontWeight: 700, fontSize: "1.1rem" }}>
                        {biz.name}
                      </div>
                      <div
                        style={{
                          fontSize: "0.82rem",
                          color: "var(--text-muted)",
                        }}
                      >
                        {biz.type_slug} · {biz.zone?.name ?? "Unknown zone"}
                      </div>
                    </div>
                    <div
                      style={{
                        display: "flex",
                        gap: "0.5rem",
                        alignItems: "center",
                      }}
                    >
                      <span
                        className={`badge ${biz.is_open ? "badge-green" : "badge-red"}`}
                      >
                        {biz.is_open ? "Open" : "Closed"}
                      </span>
                      <span
                        style={{
                          fontSize: "0.85rem",
                          color: "var(--accent-green)",
                        }}
                      >
                        Revenue 7d: {fmt(biz.revenue_7d)}
                      </span>
                    </div>
                  </div>

                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns: "1fr 1fr 1fr",
                      gap: "1rem",
                    }}
                  >
                    {/* Inventory */}
                    <div>
                      <div
                        style={{
                          fontSize: "0.75rem",
                          textTransform: "uppercase",
                          letterSpacing: "0.06em",
                          color: "var(--text-muted)",
                          marginBottom: "0.5rem",
                        }}
                      >
                        Inventory
                      </div>
                      {biz.inventory.length === 0 ? (
                        <span style={{ color: "var(--text-muted)", fontSize: "0.85rem" }}>
                          Empty
                        </span>
                      ) : (
                        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.3rem" }}>
                          {biz.inventory.map((item) => (
                            <span key={item.good_slug} className="badge badge-gray">
                              {item.good_slug} ×{item.quantity}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>

                    {/* Prices */}
                    <div>
                      <div
                        style={{
                          fontSize: "0.75rem",
                          textTransform: "uppercase",
                          letterSpacing: "0.06em",
                          color: "var(--text-muted)",
                          marginBottom: "0.5rem",
                        }}
                      >
                        Storefront Prices
                      </div>
                      {biz.storefront_prices.length === 0 ? (
                        <span style={{ color: "var(--text-muted)", fontSize: "0.85rem" }}>
                          None set
                        </span>
                      ) : (
                        <div
                          style={{
                            display: "flex",
                            flexDirection: "column",
                            gap: "0.25rem",
                          }}
                        >
                          {biz.storefront_prices.map((sp) => (
                            <div
                              key={sp.good_slug}
                              style={{ fontSize: "0.85rem" }}
                            >
                              <span style={{ color: "var(--text-secondary)" }}>
                                {sp.good_slug}
                              </span>{" "}
                              <span
                                style={{
                                  fontFamily: "var(--font-mono)",
                                  color: "var(--accent-green)",
                                }}
                              >
                                ${sp.price.toFixed(2)}
                              </span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>

                    {/* Employees */}
                    <div>
                      <div
                        style={{
                          fontSize: "0.75rem",
                          textTransform: "uppercase",
                          letterSpacing: "0.06em",
                          color: "var(--text-muted)",
                          marginBottom: "0.5rem",
                        }}
                      >
                        Employees ({biz.employees.length})
                      </div>
                      {biz.employees.length === 0 ? (
                        <span style={{ color: "var(--text-muted)", fontSize: "0.85rem" }}>
                          No active employees
                        </span>
                      ) : (
                        <div
                          style={{
                            display: "flex",
                            flexDirection: "column",
                            gap: "0.25rem",
                          }}
                        >
                          {biz.employees.slice(0, 5).map((e, i) => (
                            <div
                              key={i}
                              style={{ fontSize: "0.82rem" }}
                            >
                              <span className="badge badge-purple">
                                {e.product_slug}
                              </span>
                              <span
                                style={{
                                  marginLeft: "0.4rem",
                                  color: "var(--text-muted)",
                                }}
                              >
                                {fmt(e.wage_per_work)}/work
                              </span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>
        )}

        {/* Messages tab */}
        {tab === "messages" && (
          <div className="card" style={{ padding: 0 }}>
            <div className="card-header" style={{ padding: "1rem 1.25rem" }}>
              <span className="card-title">Inbox</span>
              {messages && (
                <span style={{ fontSize: "0.8rem", color: "var(--text-muted)" }}>
                  {messages.unread_count} unread · {messages.pagination.total} total
                </span>
              )}
            </div>
            <div style={{ padding: "0 1.25rem 1.25rem" }}>
              {!messages ? (
                <div className="loading-box">Loading messages…</div>
              ) : messages.messages.length === 0 ? (
                <div className="empty-box">No messages yet</div>
              ) : (
                <div>
                  {messages.messages.map((msg) => (
                    <div
                      key={msg.id}
                      style={{
                        padding: "0.75rem 0",
                        borderBottom: "1px solid var(--border)",
                        opacity: msg.read ? 0.7 : 1,
                      }}
                    >
                      <div
                        className="flex justify-between items-center"
                        style={{ marginBottom: "0.3rem" }}
                      >
                        <div
                          style={{
                            fontWeight: msg.read ? 400 : 700,
                            fontSize: "0.9rem",
                          }}
                        >
                          {msg.from_agent_name}
                          {!msg.read && (
                            <span
                              className="badge badge-blue"
                              style={{ marginLeft: "0.5rem" }}
                            >
                              New
                            </span>
                          )}
                        </div>
                        <span
                          style={{
                            fontSize: "0.75rem",
                            color: "var(--text-muted)",
                          }}
                        >
                          {new Date(msg.created_at).toLocaleString([], {
                            month: "short",
                            day: "numeric",
                            hour: "2-digit",
                            minute: "2-digit",
                          })}
                        </span>
                      </div>
                      <div
                        style={{
                          fontSize: "0.875rem",
                          color: "var(--text-secondary)",
                          lineHeight: 1.5,
                        }}
                      >
                        {msg.text}
                      </div>
                    </div>
                  ))}

                  {messages.pagination.total_pages > 1 && (
                    <div className="pagination">
                      <button
                        className="page-btn"
                        disabled={msgPage <= 1}
                        onClick={() => setMsgPage((p) => p - 1)}
                      >
                        ← Prev
                      </button>
                      <span
                        style={{
                          color: "var(--text-muted)",
                          fontSize: "0.875rem",
                        }}
                      >
                        Page {msgPage} of {messages.pagination.total_pages}
                      </span>
                      <button
                        className="page-btn"
                        disabled={msgPage >= messages.pagination.total_pages}
                        onClick={() => setMsgPage((p) => p + 1)}
                      >
                        Next →
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
