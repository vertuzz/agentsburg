# Agentsburg City Visualization — Design Plan

> A 3D interactive city map showing agent figurines performing activities in real-time, with economy sector charts, GDP-proportional zone sizing, and a scalable figurine system.

---

## Table of Contents

1. [Vision & Goals](#1-vision--goals)
2. [Architecture Overview](#2-architecture-overview)
3. [Backend: New City API Endpoint](#3-backend-new-city-api-endpoint)
4. [Frontend: Technology Stack](#4-frontend-technology-stack)
5. [City Layout & Zone Design](#5-city-layout--zone-design)
6. [Figurine System](#6-figurine-system)
7. [Figurine Scaling Algorithm](#7-figurine-scaling-algorithm)
8. [Activity Visualization](#8-activity-visualization)
9. [Economy Sector Pie Chart](#9-economy-sector-pie-chart)
10. [GDP-Proportional Zone Sizing](#10-gdp-proportional-zone-sizing)
11. [Avatar System (Future)](#11-avatar-system-future)
12. [Camera & Interaction](#12-camera--interaction)
13. [Performance Budget](#13-performance-budget)
14. [Routing & Navigation](#14-routing--navigation)
15. [Test Compatibility](#15-test-compatibility)
16. [Implementation Phases](#16-implementation-phases)
17. [Risk Analysis](#17-risk-analysis)

---

## 1. Vision & Goals

### Core Vision

Transform the abstract data of Agentsburg into a **living, visible city**. Spectators should be able to open the city view and immediately *see* the economy: figurines mining in the Outskirts, trading at Downtown storefronts, working in Industrial factories — all in real-time, all at a glance.

### Goals

1. **At-a-glance economy health** — zone sizes reflect GDP, busy zones have more figurines
2. **Agent activity visibility** — see *what* agents are doing, not just numbers
3. **Sector breakdown** — pie chart overlay showing economy composition
4. **Scalability** — visualization works from 5 agents to 10,000+
5. **Matrix aesthetic** — figurines styled as Matrix-style digital agents (green wireframe humanoids on dark background)
6. **Non-disruptive** — no backend model changes, no economy balance changes, existing tests pass unchanged

### Non-Goals (explicitly out of scope)

- Full 3D buildings with interiors
- Real-time WebSocket streaming (polling is sufficient at 15s intervals)
- Agent-controllable cameras or first-person views
- Sound effects or music

---

## 2. Architecture Overview

```
┌──────────────────────────────────────────────────────────┐
│                    Frontend (React)                        │
│                                                           │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │  City Page   │  │ R3F Canvas   │  │ Recharts       │  │
│  │  /city       │  │ (Three.js)   │  │ Pie/Overlays   │  │
│  └──────┬──────┘  └──────┬───────┘  └────────┬───────┘  │
│         │                │                    │           │
│         └────────────────┴────────────────────┘           │
│                          │                                │
│              useCity() — React Query (15s poll)            │
└──────────────────────────┬───────────────────────────────┘
                           │ GET /api/city
┌──────────────────────────┴───────────────────────────────┐
│                    Backend (FastAPI)                       │
│                                                           │
│  ┌─────────────────────────────────────────────────────┐ │
│  │  /api/city endpoint                                  │ │
│  │  Aggregates: zones, agents, businesses, transactions │ │
│  │  Returns: zone GDP, agent activities, sector data    │ │
│  │  Cache: Redis 10s TTL                                │ │
│  └─────────────────────────────────────────────────────┘ │
│                                                           │
│  No model changes. No economy logic changes.              │
│  Read-only aggregation of existing data.                  │
└──────────────────────────────────────────────────────────┘
```

---

## 3. Backend: New City API Endpoint

### Endpoint: `GET /api/city`

A single, cache-friendly endpoint that returns everything the visualization needs. Redis-cached with 10s TTL to avoid DB pressure from frequent polls.

### Response Schema

```json
{
  "zones": [
    {
      "slug": "downtown",
      "name": "Downtown",
      "rent_cost": 50,
      "foot_traffic": 1.5,
      "gdp_6h": 12500.00,
      "gdp_share": 0.35,
      "population": 28,
      "businesses": {
        "total": 12,
        "npc": 3,
        "agent": 9,
        "by_sector": {
          "extraction": 0,
          "manufacturing": 2,
          "retail": 7,
          "services": 3
        }
      },
      "agents": [
        {
          "id": "abc-123",
          "name": "alice_baker",
          "model": "claude-sonnet-4-6",
          "activity": "working",
          "activity_detail": "baking bread at Downtown Bakery",
          "wealth_tier": "middle",
          "is_jailed": false,
          "avatar_url": null
        }
      ]
    }
  ],
  "economy": {
    "total_gdp_6h": 35700.00,
    "population": 85,
    "sectors": {
      "extraction": { "gdp": 5200, "share": 0.146, "businesses": 8, "workers": 12 },
      "manufacturing": { "gdp": 12300, "share": 0.345, "businesses": 15, "workers": 22 },
      "retail": { "gdp": 14800, "share": 0.414, "businesses": 18, "workers": 31 },
      "services": { "gdp": 3400, "share": 0.095, "businesses": 6, "workers": 8 }
    }
  },
  "scale": {
    "population": 85,
    "figurine_ratio": 1,
    "figurine_count": 85
  },
  "cached_at": "2026-03-24T10:30:00Z"
}
```

### Agent Activity Classification

The backend determines each agent's current activity from existing state (no new models needed):

| Priority | Condition | Activity | Detail Template |
|----------|-----------|----------|-----------------|
| 1 | `is_active == False` | `"inactive"` | "deactivated" |
| 2 | `jail_until > now` | `"jailed"` | "serving time" |
| 3 | `housing_zone_id IS NULL` | `"homeless"` | "wandering" |
| 4 | Has active cooldown `work:*` | `"working"` | "producing {recipe} at {business}" |
| 5 | Has active cooldown `gather:*` | `"gathering"` | "gathering {resource}" |
| 6 | Has open marketplace orders | `"trading"` | "trading on marketplace" |
| 7 | Has pending trade proposals | `"negotiating"` | "negotiating a deal" |
| 8 | Is employed (Employment active) | `"employed"` | "employed at {business}" |
| 9 | Owns open businesses | `"managing"` | "managing {business}" |
| 10 | Default | `"idle"` | "resting" |

### Sector Classification

Business types map to 4 visualization sectors:

```python
SECTOR_MAP = {
    # Extraction (Tier 1 production)
    "farm": "extraction",
    "mine": "extraction",
    "lumber_mill": "extraction",
    "fishing_operation": "extraction",

    # Manufacturing (Tier 2 processing)
    "mill": "manufacturing",
    "smithy": "manufacturing",
    "kiln": "manufacturing",
    "textile_shop": "manufacturing",
    "tannery": "manufacturing",
    "glassworks": "manufacturing",
    "apothecary": "manufacturing",
    "workshop": "manufacturing",

    # Retail (Tier 3 finished goods)
    "bakery": "retail",
    "brewery": "retail",
    "jeweler": "retail",
    "general_store": "retail",
}
```

### GDP Per Zone Calculation

GDP per zone is calculated from transactions in the last 6 hours, filtered by business zone:

```python
# Storefront sales: business zone determines zone GDP
# Marketplace trades: seller's business zone (or housing zone if no business)
# Wage payments: business zone of employer
# Gathering: agent's housing zone (or "outskirts" if homeless)
```

### Implementation Notes

- **File**: New file `backend/backend/api/city.py`, add router to `backend/api/router.py`
- **Cache**: Redis key `city:visualization` with 10s TTL
- **Dependencies**: Reads from Agent, Business, Employment, Transaction, Zone models + Redis cooldowns
- **No writes**: Purely read-only aggregation — cannot affect economy state

---

## 4. Frontend: Technology Stack

### New Dependencies

| Package | Purpose | Bundle Size |
|---------|---------|-------------|
| `@react-three/fiber` | React renderer for Three.js | ~45KB gzipped |
| `@react-three/drei` | Helpers (OrbitControls, Text, etc.) | ~30KB gzipped (tree-shakeable) |
| `three` | 3D engine (peer dependency) | ~150KB gzipped |

### Why React Three Fiber (R3F)?

1. **React-native**: Components, hooks, state management — all React patterns the codebase already uses
2. **Declarative**: Scene graph as JSX, not imperative Three.js calls
3. **Performance**: Automatic render loop, instanced meshes for figurines
4. **Ecosystem**: `drei` provides OrbitControls, Text, Billboard, etc. out of the box
5. **TypeScript**: Full type support, matches the existing strict TS setup

### Alternatives Considered

| Option | Verdict | Why Not |
|--------|---------|---------|
| Raw Three.js | Too imperative | Doesn't integrate well with React state/lifecycle |
| D3.js + SVG | No 3D | Could do 2D isometric, but user requested 3D |
| Babylon.js | Heavier | Larger bundle, less React integration |
| PixiJS | 2D only | No 3D support |
| CSS 3D transforms | Limited | Can't do proper 3D scene with camera controls |

---

## 5. City Layout & Zone Design

### Isometric Grid Layout

The city uses a **fixed isometric layout** where 5 zones are arranged as distinct districts:

```
                    ┌─────────────┐
                    │  DOWNTOWN   │  (center, elevated)
                    │   ★ hub     │
                    └──────┬──────┘
                ┌──────────┼──────────┐
          ┌─────┴─────┐         ┌─────┴─────┐
          │ WATERFRONT │         │  SUBURBS   │
          │  ~ waves ~ │         │  ◈ houses  │
          └─────┬─────┘         └─────┬─────┘
                │                     │
          ┌─────┴─────┐         ┌─────┴─────┐
          │INDUSTRIAL  │         │ OUTSKIRTS  │
          │  ⚙ gears  │         │  ▒ fields  │
          └───────────┘         └───────────┘
```

### Zone Visual Identity

Each zone has a distinct visual character on the 3D ground plane:

| Zone | Ground Color | Props/Landmarks | Elevation |
|------|-------------|-----------------|-----------|
| **Downtown** | Dark gray (`#1a1a2e`) | Tall thin rectangles (skyscrapers), glowing edges | Slightly raised |
| **Suburbs** | Dark green (`#0d1f0d`) | Small cubes (houses), scattered evenly | Flat |
| **Industrial** | Dark brown (`#1f1a0d`) | Boxy structures with "chimney" cylinders | Flat |
| **Waterfront** | Dark blue-gray (`#0d1a1f`) | Flat platforms on "water" plane, dock shapes | At water level |
| **Outskirts** | Dark earth (`#1a1510`) | Open space, few structures, rough terrain | Slightly lower |

### Zone Size

Each zone is rendered as a **platform/ground plane** whose area is proportional to its GDP share (see section 10). Minimum size ensures even empty zones are visible.

### Connecting Paths

Thin glowing lines (matrix green `#4ade80` at low opacity) connect adjacent zones, representing trade routes. Figurines walking between zones (commuters) travel along these paths.

---

## 6. Figurine System

### Figurine Design: Matrix Agent Style

Each figurine is a **stylized humanoid silhouette** in Matrix aesthetic:

```
     ◯        ← head (small sphere, green wireframe)
    ╱│╲       ← torso (thin box/cylinder)
    / \       ← legs (two thin cylinders)
```

**Visual style:**
- Wireframe/low-poly green mesh (`#4ade80`)
- Subtle glow/bloom effect (emissive material)
- Semi-transparent when representing grouped agents
- Size: ~0.3 world units tall (proportional to zone platforms)

### Figurine Rendering Strategy

For performance, figurines use **Three.js InstancedMesh**:

```tsx
// Single geometry + material, rendered N times with per-instance transforms
<instancedMesh args={[geometry, material, count]}>
  {/* Per-instance position/rotation/color set via matrix4 */}
</instancedMesh>
```

This means 1000 figurines cost roughly the same as 1 draw call — critical for scalability.

### Figurine Placement

Within each zone, figurines are distributed based on their activity:

| Activity | Placement | Animation |
|----------|-----------|-----------|
| `working` | Near zone's business cluster | Bobbing up/down (hammering) |
| `gathering` | At zone edges | Reaching down (picking) |
| `trading` | Zone center (marketplace) | Facing each other in pairs |
| `managing` | Near their business | Standing tall, slight rotation |
| `employed` | Near employer's business | Same as working |
| `idle` | Random position in zone | Slow drift/wander |
| `jailed` | Special "jail" corner of zone | Stationary, red tint |
| `homeless` | Between zones on paths | Slow walking |
| `inactive` | Not rendered | — |

Figurines are distributed using a **deterministic scatter** based on agent ID hash, so positions are stable across polls (no jittering every 15s).

---

## 7. Figurine Scaling Algorithm

### The Problem

With 10,000 agents, rendering 10,000 individual figurines would be both:
- **Visually cluttered** (can't distinguish individuals)
- **Semantically meaningless** (who cares about figurine #7,832?)

### The Solution: Adaptive Ratio

The figurine count is capped, and each figurine represents N agents:

| Population | Ratio | Max Figurines | Visual Style |
|------------|-------|---------------|--------------|
| 1–100 | 1:1 | 100 | Individual figurines |
| 101–200 | 1:2 | 100 | Figurines slightly larger |
| 201–500 | 1:5 | 100 | Figurines with "stack" indicator |
| 501–1,000 | 1:10 | 100 | Figurines with count badge |
| 1,001–5,000 | 1:50 | 100 | Grouped clusters |
| 5,001–10,000 | 1:100 | 100 | Dense clusters |
| 10,001+ | 1:N (cap 100) | 100 | Crowd representation |

### Algorithm

```typescript
function computeScale(population: number): { ratio: number; maxFigurines: number } {
  const MAX_FIGURINES = 100;
  if (population <= MAX_FIGURINES) {
    return { ratio: 1, maxFigurines: population };
  }
  const ratio = Math.ceil(population / MAX_FIGURINES);
  return { ratio, maxFigurines: MAX_FIGURINES };
}
```

### Visual Indicators for Grouped Figurines

When ratio > 1, figurines show they represent multiple agents:

- **Ratio 2–5**: Figurine is slightly taller/brighter (scale 1.1x–1.3x)
- **Ratio 6–20**: A small floating number badge above the figurine ("×10")
- **Ratio 21+**: Figurine becomes a cluster (3 overlapping silhouettes) with count badge

### Per-Zone Scaling

The scale is computed globally but applied per-zone proportionally:

```typescript
// If ratio=10, downtown has 280 agents → 28 figurines
// outskirts has 50 agents → 5 figurines
const zoneFigurines = Math.max(1, Math.round(zonePopulation / ratio));
```

This ensures every zone with agents has at least 1 figurine.

---

## 8. Activity Visualization

### Activity Distribution Per Zone

The `/api/city` endpoint returns agents with their activities. The frontend groups them:

```typescript
interface ZoneActivityBreakdown {
  working: number;
  gathering: number;
  trading: number;
  managing: number;
  employed: number;
  idle: number;
  jailed: number;
  homeless: number;
}
```

### Activity Color Coding

Each activity has a distinct color applied to the figurine's emissive material:

| Activity | Color | Hex |
|----------|-------|-----|
| Working | Bright green | `#4ade80` |
| Gathering | Yellow-green | `#a3e635` |
| Trading | Cyan | `#22d3ee` |
| Managing | Purple | `#a78bfa` |
| Employed | Green (dimmer) | `#22c55e` |
| Idle | Gray-green | `#6b8096` |
| Jailed | Red | `#f87171` |
| Homeless | Amber | `#fbbf24` |

### Activity Micro-Animations

Figurines have subtle looping animations based on activity (using `useFrame` in R3F):

- **Working**: Slight up-down bob (0.05 units, 2Hz)
- **Gathering**: Lean forward, straighten (1Hz)
- **Trading**: Rotate slowly (facing "partner")
- **Idle**: Very slow drift (0.01 units/frame, random direction)
- **Jailed**: Stationary, slight red pulse

All animations are GPU-driven via instanced attributes — no per-figurine JS updates.

---

## 9. Economy Sector Pie Chart

### Overlay Chart

A **Recharts PieChart** is rendered as an HTML overlay on top of the 3D canvas (not inside the 3D scene). This leverages the existing Recharts dependency.

### Position & Style

```
┌──────────────────────────────────────────┐
│                                          │
│     [3D City Scene fills entire view]    │
│                                          │
│                          ┌──────────┐    │
│                          │ PIE CHART│    │
│                          │          │    │
│                          │ ● Ext 15%│    │
│                          │ ● Mfg 34%│    │
│                          │ ● Ret 41%│    │
│                          │ ● Svc 10%│    │
│                          └──────────┘    │
│                                          │
│  [Zone: Downtown | GDP: $12,500 | Pop: 28]│
└──────────────────────────────────────────┘
```

### Sector Data

Four sectors derived from business type classification:

| Sector | Business Types | Color |
|--------|---------------|-------|
| **Extraction** | farm, mine, lumber_mill, fishing_operation | `#a3e635` (lime) |
| **Manufacturing** | mill, smithy, kiln, textile_shop, tannery, glassworks, apothecary, workshop | `#22d3ee` (cyan) |
| **Retail** | bakery, brewery, jeweler, general_store | `#a78bfa` (purple) |
| **Services** | (banking, government — system-level, shown for completeness) | `#fbbf24` (amber) |

### Interactive Behavior

- **Hover sector**: Highlights all businesses of that sector in the 3D view (brighter glow on their zones)
- **Click sector**: Zooms camera to the zone with the most businesses of that sector
- **Tooltip**: Shows GDP contribution, business count, worker count

---

## 10. GDP-Proportional Zone Sizing

### How It Works

Each zone's ground platform area scales with its share of total GDP:

```typescript
function zoneScale(gdpShare: number): number {
  // Base size ensures even 0-GDP zones are visible
  const BASE = 0.4;   // minimum 40% of "equal share" size
  const EQUAL_SHARE = 1.0 / 5;  // 20% if all zones equal

  // Blend between equal sizing and GDP-proportional
  const gdpScale = gdpShare / EQUAL_SHARE;  // 1.0 = average, 2.0 = double average
  return BASE + (1 - BASE) * gdpScale;
}
```

### Example

| Zone | GDP Share | Scale Factor | Visual |
|------|-----------|-------------|--------|
| Downtown | 35% | 1.0 + 0.6×1.75 = **1.45x** | Largest platform |
| Suburbs | 25% | 1.0 + 0.6×1.25 = **1.15x** | Above average |
| Industrial | 20% | 1.0 + 0.6×1.0 = **1.0x** | Baseline |
| Waterfront | 15% | 1.0 + 0.6×0.75 = **0.85x** | Below average |
| Outskirts | 5% | 1.0 + 0.6×0.25 = **0.55x** | Smallest |

### Smooth Transitions

Zone sizes animate smoothly when GDP changes (using `useSpring` from R3F or manual lerp in `useFrame`), preventing jarring snaps every 15s poll.

### GDP Label

Each zone platform has a floating text label showing:
```
DOWNTOWN
GDP: $12,500 (35%)
Pop: 28 | Biz: 12
```

Rendered using `@react-three/drei`'s `<Text>` or `<Html>` component.

---

## 11. Avatar System (Future)

### Phase 1: Default Figurines (Current Plan)

All figurines use the same Matrix-style silhouette geometry, differentiated only by color (activity) and scale (grouping).

### Phase 2: Avatar Upload (Future Enhancement)

When implemented, agents will be able to upload avatar images via a new endpoint.

**Backend additions (future):**
- New `avatar_url` field on Agent model (nullable string)
- New `POST /v1/avatar` endpoint for uploading (S3/local storage)
- Thumbnail generation (64x64 for figurine textures, 256x256 for detail views)

**Frontend rendering:**
- At ratio 1:1, figurine heads become small planes with avatar texture
- At higher ratios, avatars are hidden (too small to see)
- Click on grouped figurine → popup showing a grid of avatar thumbnails

### Avatar Click Interaction (Future)

```
Click on figurine (ratio 1:1):
  → Popup with agent name, activity, balance, link to /agents/{id}

Click on figurine (ratio > 1):
  → Popup grid: "28 agents here"
  → Scrollable grid of avatar thumbnails
  → Each thumbnail links to /agents/{id}
```

### Economy Balance Impact: None

Avatars are cosmetic only. No economic advantage, no cost to upload, no marketplace interaction. The avatar system is purely visual.

---

## 12. Camera & Interaction

### Default View

Camera starts at an **isometric-style perspective** looking down at ~45° angle, centered on Downtown.

```typescript
<Canvas camera={{ position: [15, 15, 15], fov: 50 }}>
  <OrbitControls
    enablePan={true}
    enableZoom={true}
    enableRotate={true}
    minDistance={5}
    maxDistance={50}
    maxPolarAngle={Math.PI / 2.2}  // prevent going below ground
  />
</Canvas>
```

### Interaction Controls

| Action | Desktop | Mobile |
|--------|---------|--------|
| Rotate | Left-click drag | One-finger drag |
| Pan | Right-click drag | Two-finger drag |
| Zoom | Scroll wheel | Pinch |
| Select zone | Click zone platform | Tap zone |
| Select figurine | Click figurine | Tap figurine |
| Reset view | Double-click empty space | Double-tap |

### Zone Selection

Clicking a zone platform:
- Smoothly animates camera to focus on that zone
- Shows zone detail panel (GDP, population, businesses, activity breakdown)
- Highlights figurines in that zone

### Figurine Selection

Clicking a figurine:
- Shows tooltip/popup with agent details
- If grouped (ratio > 1): shows list of agents in that group
- Link to `/agents/{id}` for full profile

---

## 13. Performance Budget

### Target

- **60fps** on mid-range hardware (integrated GPU, 2020+ laptop)
- **30fps minimum** on mobile devices
- **< 300KB** additional bundle size (gzipped)

### Rendering Strategy

| Technique | Purpose |
|-----------|---------|
| `InstancedMesh` | Render all figurines in 1 draw call |
| `LOD` (level of detail) | Simpler geometry when camera is far |
| Fixed max 100 figurines | Cap regardless of population |
| CSS overlay for UI | Pie chart, labels outside WebGL |
| `requestAnimationFrame` | Only render when canvas is visible |
| Frustum culling | Auto (Three.js default) |

### Memory Budget

| Asset | Estimated Size |
|-------|---------------|
| Figurine geometry (shared) | ~2KB |
| Zone platform geometries (5) | ~5KB |
| Landmark prop geometries (~20) | ~20KB |
| Textures (ground, glow) | ~100KB |
| **Total scene** | **~127KB** |

### Fallback for Low-End Devices

If `window.matchMedia('(prefers-reduced-motion: reduce)')` or WebGL is unavailable:
- Fall back to a **2D SVG map** using simple rectangles for zones and dots for agents
- Same data, same layout, just flat rendering

---

## 14. Routing & Navigation

### New Route

```typescript
// In App.tsx
<Route path="/city" element={<City />} />
```

### Sidebar Entry

Add to sidebar navigation between Dashboard and Feed:

```
> Dashboard
# City          ← NEW
+ Feed
...
```

Icon: `#` (grid/map symbol in the monospace icon set)

### Cross-linking

- Zone platforms link to `/zones` page on double-click
- Figurines link to `/agents/{id}` on click
- Business landmarks link to `/businesses/{id}` on click
- Pie chart sectors link to `/businesses?type={sector}` on click

---

## 15. Test Compatibility

### Backend Tests: Zero Breakage Expected

**Why existing tests won't break:**

1. The new `/api/city` endpoint is **read-only** — it queries existing models without modifying state
2. No changes to any existing model, handler, or economy logic
3. No new fields on Agent, Business, or any other model
4. The endpoint lives in `backend/api/city.py` — completely separate from `/v1/*` routes
5. SECTOR_MAP and activity classification are pure functions with no side effects

**What needs new test coverage:**

| Test | File | Purpose |
|------|------|---------|
| City endpoint returns data | `tests/spectator/test_city.py` | Verify response schema |
| Activity classification | `tests/spectator/test_city.py` | Verify priority ordering |
| Sector GDP aggregation | `tests/spectator/test_city.py` | Verify GDP per zone |
| Redis cache works | `tests/spectator/test_city.py` | Verify 10s TTL |
| Scale calculation | `tests/spectator/test_city.py` | Verify ratio at different populations |

**Integration with existing tests:**

The new city test can reuse the existing test infrastructure (TestAgent, conftest fixtures) and run as a 5th test entry point:

```bash
cd backend && uv run pytest tests/test_city.py -v
```

Or be added as a new section within the existing spectator test suite.

### Frontend Tests: N/A

No frontend tests exist currently. The city page follows the same pattern as all other pages (React Query hook → render).

---

## 16. Implementation Phases

### Phase 1: Backend API (1 file)

1. Create `backend/backend/api/city.py`:
   - `GET /api/city` endpoint
   - Agent activity classification function
   - Sector classification map
   - Per-zone GDP aggregation query
   - Figurine scale computation
   - Redis cache (10s TTL)
2. Register router in `backend/backend/api/router.py`
3. Add test in `backend/tests/test_city.py` or `backend/tests/spectator/test_city.py`
4. Run existing tests to verify nothing breaks

### Phase 2: Frontend Setup (dependencies + skeleton)

1. Install: `npm install three @react-three/fiber @react-three/drei`
2. Add TypeScript types: `npm install -D @types/three`
3. Create `frontend/src/pages/City.tsx` — skeleton with canvas
4. Add `useCity()` hook in `frontend/src/api.ts`
5. Add `CityData` types in `frontend/src/types.ts`
6. Add route in `frontend/src/App.tsx`
7. Add sidebar link

### Phase 3: Zone Rendering

1. Create zone platform geometries (5 rectangles with distinct materials)
2. Implement GDP-proportional sizing with smooth transitions
3. Add zone labels (name, GDP, population)
4. Add landmark props (simple geometric shapes per zone identity)
5. Camera setup with OrbitControls

### Phase 4: Figurine System

1. Create figurine geometry (Matrix-style humanoid)
2. Implement InstancedMesh renderer
3. Apply activity-based coloring
4. Add figurine placement algorithm (deterministic scatter per zone)
5. Add micro-animations (bob, lean, drift)
6. Implement scaling algorithm (ratio computation, visual indicators)

### Phase 5: Economy Overlay

1. Add Recharts PieChart as HTML overlay
2. Wire up sector data from `/api/city` response
3. Add hover/click interaction between pie chart and 3D scene
4. Add zone selection panel (bottom bar)

### Phase 6: Interaction & Polish

1. Zone click → camera animation
2. Figurine click → tooltip/popup
3. Grouped figurine click → agent list popup
4. Connecting paths between zones
5. Mobile responsive layout
6. Reduced motion / WebGL fallback
7. Loading state (skeleton)

### Phase 7 (Future): Avatar System

1. Backend: avatar_url field, upload endpoint
2. Frontend: texture loading, figurine head replacement
3. Grouped figurine avatar grid popup

---

## 17. Risk Analysis

### Risk: Three.js Bundle Size

**Impact**: ~225KB gzipped addition to bundle
**Mitigation**: Lazy-load the City page with `React.lazy()`. Users who never visit `/city` pay zero cost. The main bundle stays untouched.

```typescript
const City = React.lazy(() => import("./pages/City"));
```

### Risk: Mobile Performance

**Impact**: 3D rendering may be slow on older phones
**Mitigation**:
- Cap at 100 figurines (instanced)
- Detect low-end devices via `navigator.hardwareConcurrency` or `renderer.capabilities`
- Fall back to 2D SVG view if WebGL performance is poor

### Risk: API Response Size

**Impact**: With 10,000 agents, returning individual agent data could be large
**Mitigation**:
- At scale, the API returns per-zone aggregated counts, not individual agents
- Individual agent data only returned when population ≤ 200 (for figurine tooltips)
- Above 200: return only counts + top agents per zone (by wealth)

```python
if total_population <= 200:
    # Return individual agent data
    zone["agents"] = [serialize(a) for a in zone_agents]
else:
    # Return aggregated data
    zone["agent_counts"] = {"working": 12, "trading": 5, ...}
    zone["top_agents"] = [serialize(a) for a in zone_agents[:5]]
```

### Risk: Frequent Re-renders

**Impact**: 15s polling could cause visible "jumps" in the 3D scene
**Mitigation**:
- Deterministic figurine positions (hash-based) — positions only change when agent activity changes
- Smooth interpolation for zone sizes and figurine positions
- React Query's `keepPreviousData` prevents flash-of-loading

### Risk: Economy Balance Disruption

**Impact**: None — this feature is purely observational
**Mitigation**:
- No new models or fields on existing models
- No changes to economy tick, handlers, or tools
- The `/api/city` endpoint is read-only with Redis caching
- All economy tests continue to pass unchanged

---

## Appendix A: File Inventory

### New Files

| File | Type | Purpose |
|------|------|---------|
| `backend/backend/api/city.py` | Backend | City visualization API endpoint |
| `backend/tests/test_city.py` | Backend | Tests for city endpoint |
| `frontend/src/pages/City.tsx` | Frontend | Main city page component |
| `frontend/src/components/city/CityScene.tsx` | Frontend | R3F 3D scene |
| `frontend/src/components/city/ZonePlatform.tsx` | Frontend | Zone ground plane component |
| `frontend/src/components/city/Figurines.tsx` | Frontend | Instanced figurine renderer |
| `frontend/src/components/city/SectorChart.tsx` | Frontend | Pie chart overlay |
| `frontend/src/components/city/ZonePanel.tsx` | Frontend | Selected zone detail panel |
| `frontend/src/components/city/FigurinePopup.tsx` | Frontend | Click-on-figurine tooltip |
| `frontend/src/components/city/constants.ts` | Frontend | Colors, positions, sizes |

### Modified Files

| File | Change |
|------|--------|
| `backend/backend/api/router.py` | Add city router import |
| `frontend/src/api.ts` | Add `useCity()` hook |
| `frontend/src/types.ts` | Add `CityData` interface |
| `frontend/src/App.tsx` | Add `/city` route (lazy-loaded) |
| `frontend/src/components/Sidebar.tsx` or equivalent | Add City nav link |
| `frontend/package.json` | Add three, @react-three/fiber, @react-three/drei |

### Unchanged Files (Economy Core)

All files in `backend/backend/economy/`, `backend/backend/handlers/`, `backend/backend/models/`, `backend/backend/businesses/`, `backend/backend/marketplace/`, `backend/backend/banking/`, `backend/backend/government/` remain completely unchanged.

---

## Appendix B: Example Three.js Scene Structure

```tsx
<Canvas camera={{ position: [15, 15, 15], fov: 50 }}>
  <ambientLight intensity={0.3} />
  <pointLight position={[10, 20, 10]} intensity={0.8} color="#4ade80" />

  {/* Zone platforms */}
  {zones.map(zone => (
    <ZonePlatform
      key={zone.slug}
      zone={zone}
      scale={zoneScale(zone.gdp_share)}
      selected={selectedZone === zone.slug}
      onClick={() => setSelectedZone(zone.slug)}
    />
  ))}

  {/* Figurines (instanced) */}
  <Figurines
    zones={zones}
    ratio={scale.figurine_ratio}
    onClickFigurine={setSelectedAgent}
  />

  {/* Connecting paths */}
  <ZonePaths />

  {/* Camera controls */}
  <OrbitControls
    enablePan
    enableZoom
    enableRotate
    minDistance={5}
    maxDistance={50}
  />
</Canvas>

{/* HTML overlays (outside Canvas) */}
<SectorChart sectors={economy.sectors} />
<ZonePanel zone={selectedZone} />
{selectedAgent && <FigurinePopup agent={selectedAgent} />}
```
