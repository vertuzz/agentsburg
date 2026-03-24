import { useParams, Link } from "react-router-dom";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  BarChart,
  Bar,
} from "recharts";
import { useMarketGood } from "../api";
import {
  Loading,
  ErrorMsg,
  PageHeader,
  Section,
  Card,
  StatCard,
  Grid,
  Badge,
  DetailGrid,
  KV,
} from "../components/shared";
import { fmt, fmtInt } from "../components/formatters";

export default function MarketDetail() {
  const { good } = useParams<{ good: string }>();
  const { data, isLoading, error } = useMarketGood(good!);

  if (isLoading) return <Loading text={`Loading ${good} market data`} />;
  if (error) return <ErrorMsg message={(error as Error).message} />;
  const d = data!;

  const priceData = d.price_history
    .map((p) => ({
      time: new Date(p.executed_at).toLocaleTimeString("en-US", {
        hour: "2-digit",
        minute: "2-digit",
      }),
      price: p.price,
      qty: p.quantity,
    }))
    .reverse();

  return (
    <div className="animate-fade-in">
      <PageHeader
        title={d.good.name}
        subtitle={`Tier ${d.good.tier} · Base value: ${fmt(d.good.base_value)} · ${d.good.is_gatherable ? "Gatherable" : "Crafted"}`}
        right={
          <Link to="/market" style={{ fontSize: "var(--text-xs)", color: "var(--text-secondary)" }}>
            ← Back to market
          </Link>
        }
      />

      {/* ── 24h Stats ── */}
      <Section title="24-Hour Statistics">
        <Grid cols={5}>
          <StatCard
            label="Volume"
            value={fmt(d.stats_24h.volume_value)}
            sub={`${fmtInt(d.stats_24h.volume_qty)} units`}
          />
          <StatCard label="High" value={fmt(d.stats_24h.high)} color="var(--accent)" />
          <StatCard label="Low" value={fmt(d.stats_24h.low)} color="var(--danger)" />
          <StatCard label="Average" value={fmt(d.stats_24h.average)} color="var(--cyan)" />
          <StatCard
            label="Spread"
            value={
              d.order_book.best_buy != null && d.order_book.best_sell != null
                ? fmt(d.order_book.best_sell - d.order_book.best_buy)
                : "—"
            }
            sub={`Bid: ${d.order_book.best_buy != null ? fmt(d.order_book.best_buy) : "—"} / Ask: ${d.order_book.best_sell != null ? fmt(d.order_book.best_sell) : "—"}`}
          />
        </Grid>
      </Section>

      {/* ── Price chart ── */}
      {priceData.length > 0 && (
        <Section title="Price History">
          <Card>
            <ResponsiveContainer width="100%" height={260}>
              <AreaChart data={priceData}>
                <defs>
                  <linearGradient id="priceGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#4ade80" stopOpacity={0.3} />
                    <stop offset="100%" stopColor="#4ade80" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                <XAxis dataKey="time" tick={{ fontSize: 10, fill: "var(--text-muted)" }} />
                <YAxis
                  tick={{ fontSize: 10, fill: "var(--text-muted)" }}
                  domain={["dataMin", "dataMax"]}
                />
                <Tooltip contentStyle={tooltipStyle} />
                <Area
                  type="stepAfter"
                  dataKey="price"
                  stroke="#4ade80"
                  strokeWidth={2}
                  fill="url(#priceGrad)"
                />
              </AreaChart>
            </ResponsiveContainer>
          </Card>
        </Section>
      )}

      {/* ── Volume chart ── */}
      {priceData.length > 0 && (
        <Section title="Trade Volume">
          <Card>
            <ResponsiveContainer width="100%" height={120}>
              <BarChart data={priceData}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                <XAxis dataKey="time" tick={{ fontSize: 10, fill: "var(--text-muted)" }} />
                <YAxis tick={{ fontSize: 10, fill: "var(--text-muted)" }} />
                <Tooltip contentStyle={tooltipStyle} />
                <Bar dataKey="qty" fill="#22d3ee" radius={[2, 2, 0, 0]} opacity={0.7} />
              </BarChart>
            </ResponsiveContainer>
          </Card>
        </Section>
      )}

      {/* ── Order Book ── */}
      <Section title="Order Book">
        <DetailGrid>
          {/* Buy orders */}
          <Card>
            <div
              style={{
                fontSize: "var(--text-xs)",
                color: "var(--accent)",
                textTransform: "uppercase",
                letterSpacing: "0.06em",
                marginBottom: 10,
              }}
            >
              Buy Orders (Bids)
            </div>
            {d.order_book.buy.length === 0 ? (
              <div style={{ color: "var(--text-muted)", fontSize: "var(--text-sm)" }}>
                No buy orders
              </div>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th style={obTh}>Price</th>
                    <th style={{ ...obTh, textAlign: "right" }}>Quantity</th>
                    <th style={{ ...obTh, textAlign: "right" }}>Orders</th>
                  </tr>
                </thead>
                <tbody>
                  {d.order_book.buy.map((level, i) => (
                    <tr key={i}>
                      <td style={{ ...obTd, color: "var(--accent)" }}>{fmt(level.price)}</td>
                      <td style={{ ...obTd, textAlign: "right" }}>{fmtInt(level.quantity)}</td>
                      <td style={{ ...obTd, textAlign: "right", color: "var(--text-muted)" }}>
                        {level.order_count}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>

          {/* Sell orders */}
          <Card>
            <div
              style={{
                fontSize: "var(--text-xs)",
                color: "var(--danger)",
                textTransform: "uppercase",
                letterSpacing: "0.06em",
                marginBottom: 10,
              }}
            >
              Sell Orders (Asks)
            </div>
            {d.order_book.sell.length === 0 ? (
              <div style={{ color: "var(--text-muted)", fontSize: "var(--text-sm)" }}>
                No sell orders
              </div>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th style={obTh}>Price</th>
                    <th style={{ ...obTh, textAlign: "right" }}>Quantity</th>
                    <th style={{ ...obTh, textAlign: "right" }}>Orders</th>
                  </tr>
                </thead>
                <tbody>
                  {d.order_book.sell.map((level, i) => (
                    <tr key={i}>
                      <td style={{ ...obTd, color: "var(--danger)" }}>{fmt(level.price)}</td>
                      <td style={{ ...obTd, textAlign: "right" }}>{fmtInt(level.quantity)}</td>
                      <td style={{ ...obTd, textAlign: "right", color: "var(--text-muted)" }}>
                        {level.order_count}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>
        </DetailGrid>
      </Section>

      {/* ── Good properties ── */}
      <Section title="Properties">
        <Card style={{ maxWidth: 400 }}>
          <KV label="Slug">{d.good.slug}</KV>
          <KV label="Tier">
            <Badge
              color={
                d.good.tier === 1
                  ? "var(--text-secondary)"
                  : d.good.tier === 2
                    ? "var(--cyan)"
                    : "var(--amber)"
              }
            >
              {d.good.tier}
            </Badge>
          </KV>
          <KV label="Base Value">{fmt(d.good.base_value)}</KV>
          <KV label="Storage/Unit">{d.good.storage_per_unit}</KV>
          <KV label="Gatherable">{d.good.is_gatherable ? "Yes" : "No"}</KV>
        </Card>
      </Section>
    </div>
  );
}

const tooltipStyle = {
  background: "var(--bg-elevated)",
  border: "1px solid var(--border)",
  borderRadius: 4,
  fontSize: "0.75rem",
  fontFamily: "var(--font-mono)",
};

const obTh: React.CSSProperties = {
  padding: "6px 0",
  fontSize: "var(--text-xs)",
  fontWeight: 500,
  color: "var(--text-muted)",
  borderBottom: "1px solid var(--border)",
  textTransform: "uppercase",
  letterSpacing: "0.04em",
};

const obTd: React.CSSProperties = {
  padding: "6px 0",
  fontSize: "var(--text-sm)",
  borderBottom: "1px solid var(--border)",
};
