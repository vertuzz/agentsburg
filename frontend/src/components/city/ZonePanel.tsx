import type { CityZone } from "../../types";
import { ACTIVITY_COLORS } from "./constants";
import { fmt } from "../formatters";

interface ZonePanelProps {
  zone: CityZone | null;
  onClose: () => void;
}

export default function ZonePanel({ zone, onClose }: ZonePanelProps) {
  if (!zone) return null;

  // Use pre-aggregated counts for large populations, otherwise count from agents
  let activityCounts: Record<string, number>;
  if (zone.agent_counts) {
    activityCounts = zone.agent_counts;
  } else {
    activityCounts = {};
    for (const agent of zone.agents) {
      activityCounts[agent.activity] = (activityCounts[agent.activity] || 0) + 1;
    }
  }

  return (
    <div
      style={{
        position: "absolute",
        bottom: 16,
        left: "50%",
        transform: "translateX(-50%)",
        background: "rgba(10, 10, 20, 0.9)",
        borderRadius: "var(--radius-md)",
        padding: "12px 20px",
        border: "1px solid var(--border)",
        zIndex: 10,
        pointerEvents: "auto",
        minWidth: 320,
        maxWidth: 500,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <span style={{ color: "#e2e8f0", fontWeight: 600, fontSize: "var(--text-base)" }}>
            {zone.name}
          </span>
          <span style={{ color: "#64748b", fontSize: "var(--text-xs)", marginLeft: 8 }}>
            {zone.slug}
          </span>
        </div>
        <button
          onClick={onClose}
          aria-label="Close zone panel"
          style={{
            background: "none",
            border: "none",
            color: "#64748b",
            cursor: "pointer",
            fontSize: 16,
            padding: "0 4px",
          }}
        >
          x
        </button>
      </div>

      <div
        style={{
          display: "flex",
          gap: 16,
          marginTop: 8,
          fontSize: "var(--text-sm)",
          color: "#94a3b8",
        }}
      >
        <span>
          GDP: <span style={{ color: "var(--accent)" }}>${fmt(zone.gdp_6h)}</span>
        </span>
        <span>
          Pop: <span style={{ color: "#e2e8f0" }}>{zone.population}</span>
        </span>
        <span>
          Biz: <span style={{ color: "#e2e8f0" }}>{zone.businesses.total}</span>
          <span style={{ color: "#64748b" }}>
            {" "}
            ({zone.businesses.agent}A / {zone.businesses.npc}N)
          </span>
        </span>
        <span>Rent: ${fmt(zone.rent_cost)}</span>
      </div>

      {/* Activity breakdown */}
      {Object.keys(activityCounts).length > 0 && (
        <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
          {Object.entries(activityCounts)
            .sort(([, a], [, b]) => b - a)
            .map(([activity, count]) => (
              <span
                key={activity}
                style={{
                  fontSize: "var(--text-xs)",
                  color: ACTIVITY_COLORS[activity] || "#6b8096",
                  background: "rgba(255,255,255,0.05)",
                  padding: "2px 6px",
                  borderRadius: 4,
                }}
              >
                {activity}: {count}
              </span>
            ))}
        </div>
      )}
    </div>
  );
}
