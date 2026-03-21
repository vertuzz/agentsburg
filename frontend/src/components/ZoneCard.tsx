import { Link } from "react-router-dom";
import type { ZoneInfo } from "../types";

interface ZoneCardProps {
  zone: ZoneInfo;
}

export default function ZoneCard({ zone }: ZoneCardProps) {
  return (
    <div className="zone-card">
      <div className="zone-name">{zone.name}</div>
      <div
        style={{
          fontSize: "0.75rem",
          color: "var(--text-muted)",
          marginBottom: "0.75rem",
        }}
      >
        /{zone.slug}
      </div>

      <div className="zone-stats">
        <div className="zone-stat">
          <span className="zone-stat-label">Rent</span>
          <span className="zone-stat-value">${zone.rent_cost}/h</span>
        </div>
        <div className="zone-stat">
          <span className="zone-stat-label">Population</span>
          <span className="zone-stat-value">{zone.population}</span>
        </div>
        <div className="zone-stat">
          <span className="zone-stat-label">Businesses</span>
          <span className="zone-stat-value">{zone.businesses.total}</span>
        </div>
        <div className="zone-stat">
          <span className="zone-stat-label">Traffic</span>
          <span className="zone-stat-value">{zone.foot_traffic.toFixed(1)}x</span>
        </div>
      </div>

      {zone.businesses.total > 0 && (
        <div
          style={{
            marginTop: "0.75rem",
            paddingTop: "0.75rem",
            borderTop: "1px solid var(--border)",
            fontSize: "0.8rem",
            color: "var(--text-muted)",
          }}
        >
          <span className="badge badge-purple" style={{ marginRight: "0.4rem" }}>
            {zone.businesses.agent} agent
          </span>
          <span className="badge badge-gray">
            {zone.businesses.npc} NPC
          </span>
        </div>
      )}

      {zone.top_goods.length > 0 && (
        <div style={{ marginTop: "0.75rem" }}>
          <div
            style={{
              fontSize: "0.7rem",
              textTransform: "uppercase",
              letterSpacing: "0.06em",
              color: "var(--text-muted)",
              marginBottom: "0.35rem",
            }}
          >
            Top Goods
          </div>
          <div style={{ display: "flex", gap: "0.3rem", flexWrap: "wrap" }}>
            {zone.top_goods.slice(0, 3).map((g) => (
              <Link
                key={g.good_slug}
                to={`/market/${g.good_slug}`}
                style={{ textDecoration: "none" }}
              >
                <span className="badge badge-blue">{g.good_slug}</span>
              </Link>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
