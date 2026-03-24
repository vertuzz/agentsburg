import { useState } from "react";
import { useParams, Link } from "react-router-dom";
import { useAgent } from "../api";
import {
  Loading,
  ErrorMsg,
  PageHeader,
  Section,
  Card,
  StatCard,
  Grid,
  Badge,
  KV,
  DetailGrid,
  DataTable,
} from "../components/shared";
import { fmt, fmtTime, fmtDate, slugToName, txTypeColor } from "../components/formatters";
import type { Column } from "../components/shared";

export default function AgentDetail() {
  const { id } = useParams<{ id: string }>();
  const { data, isLoading, error } = useAgent(id!);
  const [copied, setCopied] = useState(false);

  if (isLoading) return <Loading text="Loading agent profile" />;
  if (error) return <ErrorMsg message={(error as Error).message} />;
  const a = data!;

  const handleShare = () => {
    navigator.clipboard.writeText(window.location.href).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  return (
    <div className="animate-fade-in">
      <PageHeader
        title={a.name}
        subtitle={[a.model && `Model: ${a.model}`, `Joined ${fmtDate(a.created_at)}`]
          .filter(Boolean)
          .join(" · ")}
        right={
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <button
              onClick={handleShare}
              style={{
                padding: "4px 10px",
                fontSize: "var(--text-xs)",
                fontFamily: "var(--font-mono)",
                border: "1px solid var(--border)",
                borderRadius: 4,
                background: "var(--bg-elevated)",
                color: copied ? "var(--accent)" : "var(--text-secondary)",
                cursor: "pointer",
              }}
            >
              {copied ? "Copied!" : "Share"}
            </button>
            <Link
              to="/agents"
              style={{ fontSize: "var(--text-xs)", color: "var(--text-secondary)" }}
            >
              ← Back to agents
            </Link>
          </div>
        }
      />

      {/* ── Strategy & Traits ── */}
      {a.strategy_detail && (
        <Section title="Strategy & Traits">
          <Card>
            <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8 }}>
              <Badge color={strategyColor(a.strategy_detail.strategy)}>
                {slugToName(a.strategy_detail.strategy)}
              </Badge>
              {a.strategy_detail.traits.map((trait) => (
                <Badge key={trait} color="var(--text-secondary)">
                  {slugToName(trait)}
                </Badge>
              ))}
            </div>
          </Card>
        </Section>
      )}

      {/* ── Badges ── */}
      {a.badges && a.badges.length > 0 && (
        <Section title="Badges">
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
              gap: 12,
            }}
          >
            {a.badges.map((badge) => (
              <Card
                key={badge.slug}
                style={{ borderLeft: `3px solid var(--accent)`, paddingLeft: 14 }}
              >
                <div style={{ fontWeight: 500, color: "var(--text-primary)", marginBottom: 4 }}>
                  {badge.name}
                </div>
                <div style={{ fontSize: "var(--text-xs)", color: "var(--text-secondary)" }}>
                  {badge.description}
                </div>
              </Card>
            ))}
          </div>
        </Section>
      )}

      {/* ── Key stats ── */}
      <Section title="Financials">
        <Grid cols={4}>
          <StatCard
            label="Total Wealth"
            value={fmt(a.total_wealth)}
            color="var(--accent)"
            icon="$"
          />
          <StatCard label="Wallet" value={fmt(a.balance)} icon=">" />
          <StatCard label="Bank Balance" value={fmt(a.bank_balance)} color="var(--cyan)" icon="~" />
          <StatCard
            label="Bankruptcies"
            value={String(a.bankruptcy_count)}
            color={a.bankruptcy_count > 0 ? "var(--danger)" : "var(--text-muted)"}
            icon="!"
          />
        </Grid>
      </Section>

      <DetailGrid>
        {/* ── Status ── */}
        <Section title="Status">
          <Card>
            <KV label="Housing">
              {a.housing_zone ? (
                <span>
                  {a.housing_zone.name}{" "}
                  <span style={{ color: "var(--text-muted)" }}>({a.housing_zone.slug})</span>
                </span>
              ) : (
                <Badge color="var(--danger)">Homeless</Badge>
              )}
            </KV>
            <KV label="Employment">
              {a.employment ? (
                <span>
                  <Link
                    to={`/businesses/${a.employment.business_id}`}
                    style={{ color: "var(--accent)" }}
                  >
                    {a.employment.business_name}
                  </Link>
                  <span style={{ color: "var(--text-muted)" }}>
                    {" "}
                    · {slugToName(a.employment.product_slug)} · {fmt(a.employment.wage_per_work)}
                    /work
                  </span>
                </span>
              ) : (
                <Badge color="var(--text-muted)">Unemployed</Badge>
              )}
            </KV>
            <KV label="Violations">{a.criminal_record.violation_count}</KV>
            <KV label="Jailed">
              {a.criminal_record.jailed ? (
                <Badge color="var(--danger)">
                  Until {a.criminal_record.jail_until ? fmtDate(a.criminal_record.jail_until) : "?"}
                </Badge>
              ) : (
                <span style={{ color: "var(--text-muted)" }}>No</span>
              )}
            </KV>
          </Card>
        </Section>

        {/* ── Inventory ── */}
        <Section title="Inventory">
          <Card>
            {a.inventory.length === 0 ? (
              <div style={{ color: "var(--text-muted)", fontSize: "var(--text-sm)" }}>
                Empty inventory
              </div>
            ) : (
              a.inventory.map((item, i) => (
                <div
                  key={i}
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    padding: "6px 0",
                    borderBottom: "1px solid var(--border)",
                  }}
                >
                  <Link
                    to={`/market/${item.good_slug}`}
                    style={{ color: "var(--text-primary)", fontSize: "var(--text-sm)" }}
                  >
                    {slugToName(item.good_slug)}
                  </Link>
                  <span
                    style={{ color: "var(--accent)", fontWeight: 500, fontSize: "var(--text-sm)" }}
                  >
                    ×{item.quantity}
                  </span>
                </div>
              ))
            )}
          </Card>
        </Section>
      </DetailGrid>

      {/* ── Businesses ── */}
      {a.businesses.length > 0 && (
        <Section title="Owned Businesses">
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
              gap: 12,
            }}
          >
            {a.businesses.map((b) => (
              <Link to={`/businesses/${b.id}`} key={b.id} style={{ textDecoration: "none" }}>
                <Card hover>
                  <div style={{ fontWeight: 500, color: "var(--text-primary)", marginBottom: 4 }}>
                    {b.name}
                  </div>
                  <div style={{ fontSize: "var(--text-xs)", color: "var(--text-secondary)" }}>
                    {slugToName(b.type_slug)} · {slugToName(b.zone_slug)}
                  </div>
                </Card>
              </Link>
            ))}
          </div>
        </Section>
      )}

      {/* ── Recent Transactions ── */}
      <Section title="Recent Transactions">
        <DataTable
          columns={txColumns}
          data={a.transactions_recent}
          emptyText="No transactions yet"
        />
      </Section>
    </div>
  );
}

const STRATEGY_COLORS: Record<string, string> = {
  tycoon: "var(--amber)",
  aggressive_expander: "var(--danger)",
  tax_evader: "var(--danger)",
  vertical_integrator: "var(--purple)",
  market_trader: "var(--cyan)",
  conservative_saver: "var(--accent)",
  wage_earner: "var(--text-secondary)",
};

function strategyColor(strategy: string): string {
  return STRATEGY_COLORS[strategy] || "var(--text-secondary)";
}

const txColumns: Column<{
  id: string;
  type: string;
  amount: number;
  created_at: string;
  from_agent_name?: string | null;
  to_agent_name?: string | null;
}>[] = [
  {
    key: "type",
    header: "Type",
    render: (t) => <Badge color={txTypeColor(t.type)}>{t.type}</Badge>,
  },
  {
    key: "amount",
    header: "Amount",
    align: "right",
    render: (t) => (
      <span style={{ fontWeight: 500, color: "var(--text-primary)" }}>{fmt(t.amount)}</span>
    ),
  },
  {
    key: "parties",
    header: "Parties",
    render: (t) => (
      <span style={{ fontSize: "var(--text-xs)", color: "var(--text-secondary)" }}>
        {t.from_agent_name || "—"} → {t.to_agent_name || "—"}
      </span>
    ),
  },
  {
    key: "time",
    header: "Time",
    align: "right",
    render: (t) => (
      <span style={{ color: "var(--text-muted)", fontSize: "var(--text-xs)" }}>
        {fmtTime(t.created_at)}
      </span>
    ),
  },
];
