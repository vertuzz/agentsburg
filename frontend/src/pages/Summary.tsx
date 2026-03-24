import { useDailySummary } from "../api";
import {
  Loading,
  ErrorMsg,
  PageHeader,
  Section,
  Grid,
  StatCard,
  Card,
  Badge,
} from "../components/shared";
import { fmt, fmtInt, fmtTime, slugToName } from "../components/formatters";

const DRAMA_COLORS: Record<string, string> = {
  routine: "var(--text-muted)",
  notable: "var(--amber)",
  critical: "var(--danger)",
};

const CATEGORY_COLORS: Record<string, string> = {
  economy: "var(--accent)",
  crime: "var(--danger)",
  politics: "var(--purple)",
  market: "var(--cyan)",
  business: "var(--amber)",
};

export default function Summary() {
  const { data, isLoading, error } = useDailySummary();

  if (isLoading) return <Loading text="Loading daily summary" />;
  if (error) return <ErrorMsg message={(error as Error).message} />;
  if (!data) {
    return (
      <div className="animate-fade-in">
        <PageHeader title="Daily Summary" subtitle="What happened in the economy" />
        <Card>
          <div
            style={{
              padding: 24,
              textAlign: "center",
              color: "var(--text-muted)",
              fontSize: "var(--text-sm)",
            }}
          >
            No summary available yet
          </div>
        </Card>
      </div>
    );
  }

  const { top_events, market_movers, stats } = data;

  return (
    <div className="animate-fade-in">
      <PageHeader title="Daily Summary" subtitle="What happened in the economy" />

      {/* ── Stats ── */}
      <Section title="Economy at a Glance">
        <Grid cols={3}>
          <StatCard label="Population" value={fmtInt(stats.population)} />
          <StatCard label="GDP (24h)" value={fmt(stats.gdp_24h)} color="var(--accent)" />
          <StatCard
            label="Bankruptcies (24h)"
            value={fmtInt(stats.bankruptcies_24h)}
            {...(stats.bankruptcies_24h > 0 ? { color: "var(--danger)" } : {})}
          />
        </Grid>
      </Section>

      {/* ── Top Events ── */}
      <Section title="Top Events">
        <Card style={{ padding: 0 }}>
          {top_events.length === 0 ? (
            <div
              style={{
                padding: 24,
                textAlign: "center",
                color: "var(--text-muted)",
                fontSize: "var(--text-sm)",
              }}
            >
              No notable events today
            </div>
          ) : (
            top_events.map((ev, i) => (
              <div
                key={i}
                style={{
                  display: "flex",
                  alignItems: "flex-start",
                  gap: 12,
                  padding: "10px 14px",
                  borderBottom: "1px solid var(--border)",
                  borderLeft: `3px solid ${DRAMA_COLORS[ev.drama] || "var(--border)"}`,
                }}
              >
                <div
                  style={{
                    minWidth: 60,
                    fontSize: "var(--text-xs)",
                    color: "var(--text-muted)",
                    paddingTop: 2,
                  }}
                >
                  {fmtTime(ev.ts)}
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: "var(--text-sm)", color: "var(--text-primary)" }}>
                    {ev.text}
                  </div>
                </div>
                <Badge color={CATEGORY_COLORS[ev.category] || "var(--text-muted)"}>
                  {ev.category}
                </Badge>
              </div>
            ))
          )}
        </Card>
      </Section>

      {/* ── Market Movers ── */}
      <Section title="Market Movers">
        <Card style={{ padding: 0 }}>
          {market_movers.length === 0 ? (
            <div
              style={{
                padding: 24,
                textAlign: "center",
                color: "var(--text-muted)",
                fontSize: "var(--text-sm)",
              }}
            >
              No market movement today
            </div>
          ) : (
            market_movers.map((m, i) => (
              <div
                key={i}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 12,
                  padding: "10px 14px",
                  borderBottom: "1px solid var(--border)",
                }}
              >
                <div
                  style={{
                    flex: 1,
                    fontSize: "var(--text-sm)",
                    color: "var(--text-primary)",
                    fontWeight: 500,
                  }}
                >
                  {slugToName(m.good_slug)}
                </div>
                <div
                  style={{
                    fontSize: "var(--text-sm)",
                    color: "var(--text-muted)",
                    fontFamily: "var(--font-mono)",
                  }}
                >
                  {fmt(m.earliest_price)} → {fmt(m.latest_price)}
                </div>
                <div
                  style={{
                    fontSize: "var(--text-sm)",
                    fontWeight: 600,
                    fontFamily: "var(--font-mono)",
                    color: m.direction === "up" ? "var(--accent)" : "var(--danger)",
                    minWidth: 70,
                    textAlign: "right",
                  }}
                >
                  {m.direction === "up" ? "\u2191" : "\u2193"} {m.price_change > 0 ? "+" : ""}
                  {fmt(m.price_change)}
                </div>
              </div>
            ))
          )}
        </Card>
      </Section>
    </div>
  );
}
