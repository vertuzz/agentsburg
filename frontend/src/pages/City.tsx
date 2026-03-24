import { useState } from "react";
import { useCity } from "../api";
import { Loading, ErrorMsg } from "../components/shared";
import CityScene from "../components/city/CityScene";
import SectorChart from "../components/city/SectorChart";
import ZonePanel from "../components/city/ZonePanel";
import { fmt } from "../components/formatters";

export default function City() {
  const { data, isLoading, error } = useCity();
  const [selectedZone, setSelectedZone] = useState<string | null>(null);

  if (isLoading) return <Loading text="Loading city..." />;
  if (error) return <ErrorMsg message={(error as Error).message} />;
  if (!data) return null;

  const selectedZoneData = selectedZone
    ? data.zones.find((z) => z.slug === selectedZone) || null
    : null;

  return (
    <div className="animate-fade-in" style={{ height: "calc(100vh - 56px)", position: "relative" }}>
      {/* Top bar with stats */}
      <div
        style={{
          position: "absolute",
          top: 12,
          left: 12,
          zIndex: 10,
          display: "flex",
          gap: 12,
          pointerEvents: "none",
        }}
      >
        <Stat label="GDP (6h)" value={`$${fmt(data.economy.total_gdp_6h)}`} />
        <Stat label="Population" value={String(data.economy.population)} />
        <Stat
          label="Figurines"
          value={`${data.scale.figurine_count} (1:${data.scale.figurine_ratio})`}
        />
      </div>

      {/* 3D Scene */}
      <CityScene data={data} selectedZone={selectedZone} onSelectZone={setSelectedZone} />

      {/* Sector pie chart overlay */}
      <SectorChart sectors={data.economy.sectors} />

      {/* Zone detail panel */}
      <ZonePanel zone={selectedZoneData} onClose={() => setSelectedZone(null)} />

      {/* WebGL fallback notice */}
      <noscript>
        <div style={{ padding: 24, color: "#94a3b8" }}>
          City visualization requires JavaScript and WebGL.
        </div>
      </noscript>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div
      style={{
        background: "rgba(10, 10, 20, 0.85)",
        borderRadius: "var(--radius-md)",
        padding: "6px 12px",
        border: "1px solid var(--border)",
        fontSize: "var(--text-xs)",
      }}
    >
      <div
        style={{
          color: "#64748b",
          fontSize: 9,
          textTransform: "uppercase",
          letterSpacing: "0.05em",
        }}
      >
        {label}
      </div>
      <div style={{ color: "var(--accent)", fontWeight: 600 }}>{value}</div>
    </div>
  );
}
