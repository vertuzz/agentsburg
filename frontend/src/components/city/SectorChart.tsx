import { PieChart, Pie, Cell, Tooltip, Legend } from "recharts";
import type { CitySector } from "../../types";
import { SECTOR_COLORS } from "./constants";
import { fmt, fmtPct } from "../formatters";

interface SectorChartProps {
  sectors: Record<string, CitySector>;
}

export default function SectorChart({ sectors }: SectorChartProps) {
  const data = Object.entries(sectors)
    .filter(([, s]) => s.businesses > 0 || s.gdp > 0)
    .map(([name, s]) => ({
      name: name.charAt(0).toUpperCase() + name.slice(1),
      value: s.gdp,
      share: s.share,
      businesses: s.businesses,
      workers: s.workers,
      slug: name,
    }));

  if (data.length === 0) return null;

  return (
    <div
      style={{
        position: "absolute",
        top: 16,
        right: 16,
        background: "rgba(10, 10, 20, 0.85)",
        borderRadius: "var(--radius-md)",
        padding: 12,
        border: "1px solid var(--border)",
        zIndex: 10,
        pointerEvents: "auto",
      }}
    >
      <div
        style={{
          fontSize: "var(--text-xs)",
          color: "var(--text-secondary)",
          marginBottom: 4,
          fontWeight: 600,
          textTransform: "uppercase",
          letterSpacing: "0.05em",
        }}
      >
        Economy Sectors
      </div>
      <PieChart width={180} height={160}>
        <Pie
          data={data}
          cx={85}
          cy={70}
          innerRadius={30}
          outerRadius={55}
          dataKey="value"
          stroke="none"
        >
          {data.map((entry) => (
            <Cell key={entry.slug} fill={SECTOR_COLORS[entry.slug] || "#6b8096"} />
          ))}
        </Pie>
        <Tooltip
          contentStyle={{
            background: "rgba(10, 10, 20, 0.95)",
            border: "1px solid var(--border)",
            borderRadius: 6,
            fontSize: 11,
            color: "#e2e8f0",
          }}
          formatter={(_value: number, _name: string, props) => {
            const d = props?.payload as (typeof data)[number] | undefined;
            if (!d) return [String(_value), _name];
            return [`$${fmt(d.value)} (${fmtPct(d.share)})`, d.name];
          }}
        />
        <Legend
          wrapperStyle={{ fontSize: 10, color: "#94a3b8" }}
          formatter={(value: string, entry) => {
            const d = data.find((dd) => dd.name === value);
            const color = (entry as { color?: string }).color || "#94a3b8";
            return (
              <span style={{ color, fontSize: 10 }}>
                {value} {d ? `${Math.round(d.share * 100)}%` : ""}
              </span>
            );
          }}
        />
      </PieChart>
    </div>
  );
}
