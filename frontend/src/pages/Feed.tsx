import { useState } from "react";
import { useFeed } from "../api";
import { Loading, ErrorMsg, PageHeader, Card, Badge, Section } from "../components/shared";
import { fmtTime } from "../components/formatters";

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

const DRAMA_OPTIONS = ["routine", "notable", "critical"] as const;
const CATEGORY_OPTIONS = ["economy", "crime", "politics", "market", "business"] as const;

export default function Feed() {
  const [minDrama, setMinDrama] = useState<string>("routine");
  const [category, setCategory] = useState<string | undefined>(undefined);
  const feed = useFeed(100, minDrama, category);

  if (feed.isLoading) return <Loading text="Loading event feed" />;
  if (feed.error) return <ErrorMsg message={(feed.error as Error).message} />;

  const events = feed.data?.events || [];
  const pulse = feed.data?.pulse;

  return (
    <div className="animate-fade-in">
      <PageHeader
        title="Live Feed"
        subtitle="Real-time narrative events from the economy"
        right={
          pulse && (
            <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
              <span style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>
                {pulse.count_1h} events/hr
              </span>
              <span style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>
                {pulse.count_24h} events/day
              </span>
            </div>
          )
        }
      />

      {/* Filters */}
      <Section title="Filters">
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 4 }}>
          <span
            style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)", alignSelf: "center" }}
          >
            Drama:
          </span>
          {DRAMA_OPTIONS.map((d) => (
            <button
              key={d}
              onClick={() => setMinDrama(d)}
              style={{
                padding: "4px 10px",
                fontSize: "var(--text-xs)",
                fontFamily: "var(--font-mono)",
                border: `1px solid ${minDrama === d ? DRAMA_COLORS[d] : "var(--border)"}`,
                borderRadius: 4,
                background: minDrama === d ? "var(--bg-elevated)" : "transparent",
                color: minDrama === d ? DRAMA_COLORS[d] : "var(--text-muted)",
                cursor: "pointer",
              }}
            >
              {d}+
            </button>
          ))}
          <span
            style={{
              fontSize: "var(--text-xs)",
              color: "var(--text-muted)",
              alignSelf: "center",
              marginLeft: 12,
            }}
          >
            Category:
          </span>
          <button
            onClick={() => setCategory(undefined)}
            style={{
              padding: "4px 10px",
              fontSize: "var(--text-xs)",
              fontFamily: "var(--font-mono)",
              border: `1px solid ${!category ? "var(--accent)" : "var(--border)"}`,
              borderRadius: 4,
              background: !category ? "var(--bg-elevated)" : "transparent",
              color: !category ? "var(--accent)" : "var(--text-muted)",
              cursor: "pointer",
            }}
          >
            all
          </button>
          {CATEGORY_OPTIONS.map((c) => (
            <button
              key={c}
              onClick={() => setCategory(category === c ? undefined : c)}
              style={{
                padding: "4px 10px",
                fontSize: "var(--text-xs)",
                fontFamily: "var(--font-mono)",
                border: `1px solid ${category === c ? CATEGORY_COLORS[c] : "var(--border)"}`,
                borderRadius: 4,
                background: category === c ? "var(--bg-elevated)" : "transparent",
                color: category === c ? CATEGORY_COLORS[c] : "var(--text-muted)",
                cursor: "pointer",
              }}
            >
              {c}
            </button>
          ))}
        </div>
      </Section>

      {/* Event timeline */}
      <Card style={{ padding: 0 }}>
        {events.length === 0 ? (
          <div
            style={{
              padding: 24,
              textAlign: "center",
              color: "var(--text-muted)",
              fontSize: "var(--text-sm)",
            }}
          >
            No events yet. Events will appear as the economy ticks.
          </div>
        ) : (
          events.map((ev, i) => (
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
    </div>
  );
}
