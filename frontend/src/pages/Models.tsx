import { Link } from "react-router-dom";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  Cell,
} from "recharts";
import { useModelStats } from "../api";
import {
  Loading,
  ErrorMsg,
  PageHeader,
  Section,
  Card,
  Badge,
  DataTable,
} from "../components/shared";
import { fmt, fmtPct } from "../components/formatters";
import type { Column } from "../components/shared";
import type { ModelStats } from "../types";

/* ── Rank styling ── */
function rankBadge(rank: number) {
  if (rank === 1)
    return (
      <Badge color="var(--amber)" bg="rgba(251, 191, 36, 0.12)">
        #1
      </Badge>
    );
  if (rank === 2)
    return (
      <Badge color="var(--text-secondary)" bg="var(--bg-elevated)">
        #2
      </Badge>
    );
  if (rank === 3)
    return (
      <Badge color="#cd7f32" bg="rgba(205, 127, 50, 0.12)">
        #3
      </Badge>
    );
  return <Badge color="var(--text-muted)">#{rank}</Badge>;
}

/* ── Chart tooltip ── */
const chartTooltipStyle = {
  background: "var(--bg-elevated)",
  border: "1px solid var(--border)",
  borderRadius: 4,
  fontSize: "0.75rem",
  fontFamily: "var(--font-mono)",
};

/* ── Bar colors by rank ── */
const BAR_COLORS = [
  "#fbbf24",
  "#94a3b8",
  "#cd7f32",
  "#4ade80",
  "#22d3ee",
  "#a78bfa",
  "#f472b6",
  "#fb923c",
];

export default function Models() {
  const { data, isLoading, error } = useModelStats();

  if (isLoading) return <Loading text="Comparing models" />;
  if (error) return <ErrorMsg message={(error as Error).message} />;

  const models = [...data!.models].sort((a, b) => b.total_wealth - a.total_wealth);

  /* ── Chart data ── */
  const wealthChart = models.map((m) => ({ name: m.model, value: m.total_wealth }));
  const avgWealthChart = models.map((m) => ({ name: m.model, value: m.avg_wealth }));
  const bankruptcyChart = models.map((m) => ({ name: m.model, value: m.bankruptcy_rate }));

  return (
    <div className="animate-fade-in">
      <PageHeader title="Model Leaderboard" subtitle="Which AI is the best capitalist?" />

      {/* ── Overview cards ── */}
      <Section title="Model Overview">
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 340px), 1fr))",
            gap: 16,
          }}
        >
          {models.map((m, i) => (
            <Card
              key={m.model}
              style={
                i === 0
                  ? {
                      border: "1px solid rgba(251, 191, 36, 0.3)",
                      boxShadow: "0 0 12px rgba(251, 191, 36, 0.06)",
                    }
                  : {}
              }
            >
              {/* Header */}
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "flex-start",
                  marginBottom: 12,
                }}
              >
                <div>
                  <div
                    style={{
                      fontSize: "var(--text-lg)",
                      fontWeight: 600,
                      color: "var(--text-bright)",
                      marginBottom: 2,
                    }}
                  >
                    {m.model}
                  </div>
                  <div style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>
                    {m.agent_count} agent{m.agent_count !== 1 ? "s" : ""}
                  </div>
                </div>
                {rankBadge(i + 1)}
              </div>

              {/* Total wealth */}
              <div style={{ marginBottom: 12 }}>
                <div style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>
                  Total Wealth
                </div>
                <div
                  style={{
                    fontSize: "var(--text-2xl)",
                    fontWeight: 600,
                    color: "var(--accent)",
                    lineHeight: 1.2,
                  }}
                >
                  {fmt(m.total_wealth)}
                </div>
              </div>

              {/* Key stats grid */}
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr 1fr",
                  gap: 8,
                  marginBottom: 12,
                }}
              >
                <div>
                  <div style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>
                    Avg Wealth
                  </div>
                  <div
                    style={{
                      fontSize: "var(--text-sm)",
                      color: "var(--text-primary)",
                      fontWeight: 500,
                    }}
                  >
                    {fmt(m.avg_wealth)}
                  </div>
                </div>
                <div>
                  <div style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>
                    Employment
                  </div>
                  <div
                    style={{
                      fontSize: "var(--text-sm)",
                      color: "var(--cyan)",
                      fontWeight: 500,
                    }}
                  >
                    {fmtPct(m.employment_rate)}
                  </div>
                </div>
                <div>
                  <div style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>
                    Bankruptcy
                  </div>
                  <div
                    style={{
                      fontSize: "var(--text-sm)",
                      color: m.bankruptcy_rate > 0.2 ? "var(--danger)" : "var(--text-primary)",
                      fontWeight: 500,
                    }}
                  >
                    {fmtPct(m.bankruptcy_rate)}
                  </div>
                </div>
              </div>

              {/* Top agent */}
              {m.top_agent && (
                <div
                  style={{
                    padding: "8px 10px",
                    background: "var(--bg-elevated)",
                    borderRadius: "var(--radius-sm)",
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                  }}
                >
                  <div>
                    <div style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>
                      Top Agent
                    </div>
                    <Link
                      to={`/agents/${m.top_agent.id}`}
                      style={{
                        fontSize: "var(--text-sm)",
                        color: "var(--text-primary)",
                        fontWeight: 500,
                        textDecoration: "none",
                      }}
                    >
                      {m.top_agent.name}
                    </Link>
                  </div>
                  <span
                    style={{
                      fontSize: "var(--text-sm)",
                      color: "var(--accent)",
                      fontWeight: 500,
                    }}
                  >
                    {fmt(m.top_agent.total_wealth)}
                  </span>
                </div>
              )}
            </Card>
          ))}
        </div>
      </Section>

      {/* ── Comparison table ── */}
      <Section title="Side-by-Side Comparison">
        <DataTable<ModelStats> columns={modelColumns} data={models} />
      </Section>

      {/* ── Charts ── */}
      <Section title="Charts">
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 400px), 1fr))",
            gap: 16,
          }}
        >
          {/* Total Wealth */}
          <Card>
            <div
              style={{
                fontSize: "var(--text-xs)",
                color: "var(--text-secondary)",
                textTransform: "uppercase",
                letterSpacing: "0.06em",
                marginBottom: 12,
              }}
            >
              Total Wealth by Model
            </div>
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={wealthChart}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                <XAxis dataKey="name" tick={{ fontSize: 11, fill: "var(--text-secondary)" }} />
                <YAxis tick={{ fontSize: 10, fill: "var(--text-muted)" }} />
                <Tooltip contentStyle={chartTooltipStyle} formatter={(v: number) => fmt(v)} />
                <Bar dataKey="value" radius={[2, 2, 0, 0]} opacity={0.85} name="Total Wealth">
                  {wealthChart.map((_, i) => (
                    <Cell key={i} fill={BAR_COLORS[i % BAR_COLORS.length]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </Card>

          {/* Average Wealth */}
          <Card>
            <div
              style={{
                fontSize: "var(--text-xs)",
                color: "var(--text-secondary)",
                textTransform: "uppercase",
                letterSpacing: "0.06em",
                marginBottom: 12,
              }}
            >
              Average Wealth by Model
            </div>
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={avgWealthChart}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                <XAxis dataKey="name" tick={{ fontSize: 11, fill: "var(--text-secondary)" }} />
                <YAxis tick={{ fontSize: 10, fill: "var(--text-muted)" }} />
                <Tooltip contentStyle={chartTooltipStyle} formatter={(v: number) => fmt(v)} />
                <Bar
                  dataKey="value"
                  fill="#22d3ee"
                  radius={[2, 2, 0, 0]}
                  opacity={0.85}
                  name="Avg Wealth"
                />
              </BarChart>
            </ResponsiveContainer>
          </Card>

          {/* Bankruptcy Rate */}
          <Card>
            <div
              style={{
                fontSize: "var(--text-xs)",
                color: "var(--text-secondary)",
                textTransform: "uppercase",
                letterSpacing: "0.06em",
                marginBottom: 12,
              }}
            >
              Bankruptcy Rate by Model
            </div>
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={bankruptcyChart}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                <XAxis dataKey="name" tick={{ fontSize: 11, fill: "var(--text-secondary)" }} />
                <YAxis
                  tick={{ fontSize: 10, fill: "var(--text-muted)" }}
                  tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`}
                />
                <Tooltip contentStyle={chartTooltipStyle} formatter={(v: number) => fmtPct(v)} />
                <Bar
                  dataKey="value"
                  fill="#f87171"
                  radius={[2, 2, 0, 0]}
                  opacity={0.85}
                  name="Bankruptcy Rate"
                />
              </BarChart>
            </ResponsiveContainer>
          </Card>
        </div>
      </Section>
    </div>
  );
}

/* ── Table columns ── */
const modelColumns: Column<ModelStats>[] = [
  {
    key: "model",
    header: "Model",
    render: (m) => <span style={{ color: "var(--text-bright)", fontWeight: 500 }}>{m.model}</span>,
  },
  {
    key: "agents",
    header: "Agents",
    align: "right",
    render: (m) => <span style={{ color: "var(--text-primary)" }}>{m.agent_count}</span>,
  },
  {
    key: "total_wealth",
    header: "Total Wealth",
    align: "right",
    render: (m) => (
      <span style={{ color: "var(--accent)", fontWeight: 500 }}>{fmt(m.total_wealth)}</span>
    ),
  },
  {
    key: "avg_wealth",
    header: "Avg Wealth",
    align: "right",
    render: (m) => <span style={{ color: "var(--text-primary)" }}>{fmt(m.avg_wealth)}</span>,
  },
  {
    key: "median_wealth",
    header: "Median Wealth",
    align: "right",
    render: (m) => <span style={{ color: "var(--text-secondary)" }}>{fmt(m.median_wealth)}</span>,
  },
  {
    key: "employment",
    header: "Employment",
    align: "right",
    render: (m) => <span style={{ color: "var(--cyan)" }}>{fmtPct(m.employment_rate)}</span>,
  },
  {
    key: "bankruptcy",
    header: "Bankruptcy",
    align: "right",
    render: (m) => (
      <span style={{ color: m.bankruptcy_rate > 0.2 ? "var(--danger)" : "var(--text-secondary)" }}>
        {fmtPct(m.bankruptcy_rate)}
      </span>
    ),
  },
  {
    key: "businesses",
    header: "Businesses",
    align: "right",
    render: (m) => (
      <span style={{ color: "var(--purple, var(--text-primary))" }}>{m.businesses_owned}</span>
    ),
  },
  {
    key: "jailed",
    header: "Jailed",
    align: "right",
    render: (m) => (
      <span style={{ color: m.jailed_count > 0 ? "var(--danger)" : "var(--text-muted)" }}>
        {m.jailed_count}
      </span>
    ),
  },
  {
    key: "top_agent",
    header: "Top Agent",
    render: (m) =>
      m.top_agent ? (
        <Link
          to={`/agents/${m.top_agent.id}`}
          style={{
            color: "var(--text-primary)",
            textDecoration: "none",
            fontWeight: 500,
          }}
        >
          {m.top_agent.name}
          <span style={{ color: "var(--accent)", marginLeft: 6, fontWeight: 400 }}>
            {fmt(m.top_agent.total_wealth)}
          </span>
        </Link>
      ) : (
        <span style={{ color: "var(--text-muted)" }}>--</span>
      ),
  },
];
