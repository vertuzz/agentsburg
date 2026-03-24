/** City visualization constants — colors, positions, sizes. */

// ── Activity colors (emissive hex for figurine materials) ──
export const ACTIVITY_COLORS: Record<string, string> = {
  working: "#4ade80",
  gathering: "#a3e635",
  trading: "#22d3ee",
  managing: "#a78bfa",
  employed: "#22c55e",
  idle: "#6b8096",
  jailed: "#f87171",
  homeless: "#fbbf24",
  negotiating: "#38bdf8",
  inactive: "#334155",
};

// ── Sector colors (for pie chart) ──
export const SECTOR_COLORS: Record<string, string> = {
  extraction: "#a3e635",
  manufacturing: "#22d3ee",
  retail: "#a78bfa",
  services: "#fbbf24",
};

// ── Zone visual identity ──
export const ZONE_CONFIG: Record<
  string,
  { color: string; elevation: number; position: [number, number, number] }
> = {
  downtown: { color: "#1a1a2e", elevation: 0.3, position: [0, 0, 0] },
  suburbs: { color: "#0d1f0d", elevation: 0, position: [6, 0, -4] },
  industrial: { color: "#1f1a0d", elevation: 0, position: [-6, 0, -4] },
  waterfront: { color: "#0d1a1f", elevation: -0.1, position: [-6, 0, 4] },
  outskirts: { color: "#1a1510", elevation: -0.15, position: [6, 0, 4] },
};

// ── Zone connections (pairs of zone slugs for path lines) ──
export const ZONE_CONNECTIONS: [string, string][] = [
  ["downtown", "suburbs"],
  ["downtown", "industrial"],
  ["downtown", "waterfront"],
  ["downtown", "outskirts"],
  ["suburbs", "outskirts"],
  ["industrial", "waterfront"],
];

// ── Figurine geometry dimensions ──
export const FIGURINE_HEIGHT = 0.3;
export const FIGURINE_HEAD_RADIUS = 0.04;
export const FIGURINE_BODY_HEIGHT = 0.15;
export const FIGURINE_LEG_HEIGHT = 0.1;

// ── Zone platform base size (before GDP scaling) ──
export const ZONE_BASE_SIZE = 4;

// ── Camera defaults ──
export const CAMERA_POSITION: [number, number, number] = [15, 15, 15];
export const CAMERA_FOV = 50;
