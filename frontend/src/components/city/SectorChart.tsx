import { PieChart, Pie, Cell, Tooltip } from "recharts";
import type { CitySector } from "../../types";
import { SECTOR_COLORS } from "./constants";
import { fmt, fmtPct } from "../formatters";

const CHART_W = 160;

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
        width: CHART_W + 24,
        maxWidth: "calc(100vw - 32px)",
        background: "rgba(10, 10, 20, 0.85)",
        borderRadius: "var(--radius-md)",
        padding: 12,
        border: "1px solid var(--border)",
        zIndex: 10,
        pointerEvents: "auto",
        overflow: "hidden",
        boxSizing: "border-box",
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
      <PieChart width={CHART_W} height={110}>
        <Pie
          data={data}
          cx={CHART_W / 2}
          cy={50}
          innerRadius={25}
          outerRadius={45}
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
      </PieChart>
      {/* Custom legend — constrained to container width */}
      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        {data.map((d) => (
          <div
            key={d.slug}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 4,
              minWidth: 0,
            }}
          >
            <span
              style={{
                width: 8,
                height: 8,
                borderRadius: 2,
                flexShrink: 0,
                background: SECTOR_COLORS[d.slug] || "#6b8096",
              }}
            />
            <span
              style={{
                fontSize: 10,
                color: SECTOR_COLORS[d.slug] || "#94a3b8",
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
                minWidth: 0,
              }}
            >
              {d.name} {Math.round(d.share * 100)}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
