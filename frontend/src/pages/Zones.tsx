import { Link } from "react-router-dom";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";
import { useZones } from "../api";
import { Loading, ErrorMsg, PageHeader, Section, Card, Badge } from "../components/shared";
import { fmt, slugToName } from "../components/formatters";

export default function Zones() {
  const { data, isLoading, error } = useZones();

  if (isLoading) return <Loading text="Mapping zones" />;
  if (error) return <ErrorMsg message={(error as Error).message} />;
  const zones = data!.zones;

  const chartData = zones.map((z) => ({
    name: z.name,
    population: z.population,
    businesses: z.businesses.total,
  }));

  return (
    <div className="animate-fade-in">
      <PageHeader title="Zones" subtitle="Geographic regions of the economy" />

      {/* ── Overview chart ── */}
      <Section title="Population & Business Distribution">
        <Card>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis dataKey="name" tick={{ fontSize: 11, fill: "var(--text-secondary)" }} />
              <YAxis tick={{ fontSize: 10, fill: "var(--text-muted)" }} />
              <Tooltip
                contentStyle={{
                  background: "var(--bg-elevated)",
                  border: "1px solid var(--border)",
                  borderRadius: 4,
                  fontSize: "0.75rem",
                  fontFamily: "var(--font-mono)",
                }}
              />
              <Bar
                dataKey="population"
                fill="#4ade80"
                radius={[2, 2, 0, 0]}
                opacity={0.8}
                name="Population"
              />
              <Bar
                dataKey="businesses"
                fill="#22d3ee"
                radius={[2, 2, 0, 0]}
                opacity={0.8}
                name="Businesses"
              />
            </BarChart>
          </ResponsiveContainer>
        </Card>
      </Section>

      {/* ── Zone cards ── */}
      <Section title="Zone Details">
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 340px), 1fr))",
            gap: 16,
          }}
        >
          {zones.map((z) => (
            <Card key={z.id}>
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
                    {z.name}
                  </div>
                  <div style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>
                    {z.slug}
                  </div>
                </div>
                <Badge color="var(--accent)">{z.population} residents</Badge>
              </div>

              {/* Stats grid */}
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
                    Rent/hr
                  </div>
                  <div
                    style={{ fontSize: "var(--text-sm)", color: "var(--amber)", fontWeight: 500 }}
                  >
                    {fmt(z.rent_cost)}
                  </div>
                </div>
                <div>
                  <div style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>
                    Traffic
                  </div>
                  <div
                    style={{
                      fontSize: "var(--text-sm)",
                      color: "var(--text-primary)",
                      fontWeight: 500,
                    }}
                  >
                    {z.foot_traffic}×
                  </div>
                </div>
                <div>
                  <div style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>
                    Demand
                  </div>
                  <div
                    style={{
                      fontSize: "var(--text-sm)",
                      color: "var(--text-primary)",
                      fontWeight: 500,
                    }}
                  >
                    {z.demand_multiplier}×
                  </div>
                </div>
              </div>

              {/* Business counts */}
              <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
                <Badge color="var(--cyan)">{z.businesses.total} businesses</Badge>
                <Badge color="var(--text-muted)">{z.businesses.agent} agent</Badge>
                <Badge color="var(--text-muted)">{z.businesses.npc} NPC</Badge>
              </div>

              {/* Allowed types */}
              <div style={{ marginBottom: 8 }}>
                <div
                  style={{
                    fontSize: "var(--text-xs)",
                    color: "var(--text-muted)",
                    marginBottom: 4,
                  }}
                >
                  Allowed business types
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                  {(z.allowed_business_types || []).map((t) => (
                    <span
                      key={t}
                      style={{
                        fontSize: "var(--text-xs)",
                        padding: "1px 6px",
                        background: "var(--bg-elevated)",
                        borderRadius: "var(--radius-sm)",
                        color: "var(--text-secondary)",
                      }}
                    >
                      {slugToName(t)}
                    </span>
                  ))}
                </div>
              </div>

              {/* Top goods */}
              {z.top_goods.length > 0 && (
                <div>
                  <div
                    style={{
                      fontSize: "var(--text-xs)",
                      color: "var(--text-muted)",
                      marginBottom: 4,
                    }}
                  >
                    Top goods by revenue
                  </div>
                  {z.top_goods.slice(0, 3).map((g, i) => (
                    <div
                      key={i}
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        fontSize: "var(--text-xs)",
                        padding: "2px 0",
                      }}
                    >
                      <Link
                        to={`/market/${g.good_slug}`}
                        style={{ color: "var(--text-secondary)" }}
                      >
                        {slugToName(g.good_slug)}
                      </Link>
                      <span style={{ color: "var(--accent)" }}>{fmt(g.revenue)}</span>
                    </div>
                  ))}
                </div>
              )}
            </Card>
          ))}
        </div>
      </Section>
    </div>
  );
}
