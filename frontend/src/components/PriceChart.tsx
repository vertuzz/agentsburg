import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";
import type { PricePoint } from "../types";

interface PriceChartProps {
  data: PricePoint[];
  title?: string;
  height?: number;
}

function formatTime(isoString: string): string {
  const d = new Date(isoString);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

interface TooltipPayloadItem {
  value: number;
  payload: PricePoint;
}

interface CustomTooltipProps {
  active?: boolean;
  payload?: TooltipPayloadItem[];
}

function CustomTooltip({ active, payload }: CustomTooltipProps) {
  if (!active || !payload?.length) return null;
  const point = payload[0];
  return (
    <div
      style={{
        background: "var(--bg-card)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-sm)",
        padding: "0.5rem 0.75rem",
        fontSize: "0.85rem",
      }}
    >
      <div style={{ color: "var(--accent-blue)", fontWeight: 600 }}>
        ${point.value.toFixed(2)}
      </div>
      <div style={{ color: "var(--text-muted)", fontSize: "0.75rem" }}>
        qty: {point.payload.quantity}
      </div>
      <div style={{ color: "var(--text-muted)", fontSize: "0.75rem" }}>
        {formatTime(point.payload.executed_at)}
      </div>
    </div>
  );
}

export default function PriceChart({
  data,
  title,
  height = 220,
}: PriceChartProps) {
  if (data.length === 0) {
    return (
      <div className="chart-wrapper">
        {title && <div className="chart-title">{title}</div>}
        <div className="empty-box">No trades yet</div>
      </div>
    );
  }

  const prices = data.map((d) => d.price);
  const minPrice = Math.min(...prices);
  const maxPrice = Math.max(...prices);
  const avgPrice = prices.reduce((a, b) => a + b, 0) / prices.length;

  // Downsample if too many points (chart performance)
  const maxPoints = 80;
  const displayData =
    data.length > maxPoints
      ? data.filter((_, i) => i % Math.ceil(data.length / maxPoints) === 0)
      : data;

  return (
    <div className="chart-wrapper">
      {title && <div className="chart-title">{title}</div>}
      <ResponsiveContainer width="100%" height={height}>
        <LineChart
          data={displayData}
          margin={{ top: 4, right: 8, left: 0, bottom: 4 }}
        >
          <CartesianGrid
            strokeDasharray="3 3"
            stroke="var(--border)"
            opacity={0.5}
          />
          <XAxis
            dataKey="executed_at"
            tickFormatter={formatTime}
            tick={{ fill: "var(--text-muted)", fontSize: 10 }}
            axisLine={{ stroke: "var(--border)" }}
            tickLine={false}
            minTickGap={40}
          />
          <YAxis
            domain={[minPrice * 0.95, maxPrice * 1.05]}
            tick={{ fill: "var(--text-muted)", fontSize: 10 }}
            axisLine={false}
            tickLine={false}
            tickFormatter={(v) => `$${v.toFixed(0)}`}
            width={50}
          />
          <Tooltip content={<CustomTooltip />} />
          <ReferenceLine
            y={avgPrice}
            stroke="var(--accent-yellow)"
            strokeDasharray="4 4"
            opacity={0.5}
          />
          <Line
            type="monotone"
            dataKey="price"
            stroke="var(--accent-blue)"
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4, fill: "var(--accent-blue)" }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
