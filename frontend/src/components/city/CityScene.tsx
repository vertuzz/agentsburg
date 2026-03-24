import { Suspense, useMemo } from "react";
import { Canvas } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import type { CityData } from "../../types";
import ZonePlatform from "./ZonePlatform";
import Figurines from "./Figurines";
import { ZONE_CONFIG, ZONE_CONNECTIONS, CAMERA_POSITION, CAMERA_FOV } from "./constants";
import * as THREE from "three";

interface CitySceneProps {
  data: CityData;
  selectedZone: string | null;
  onSelectZone: (slug: string | null) => void;
}

/** Pre-computed path data for zone connections (avoids per-frame allocations). */
interface PathSegment {
  center: [number, number, number];
  rotation: [number, number, number];
  length: number;
}

/** Glowing connection lines between zones. */
function ZonePaths() {
  const segments: PathSegment[] = useMemo(() => {
    return ZONE_CONNECTIONS.map(([a, b]) => {
      const ca = ZONE_CONFIG[a] || ZONE_CONFIG.outskirts;
      const cb = ZONE_CONFIG[b] || ZONE_CONFIG.outskirts;
      const start = new THREE.Vector3(...ca.position).setY(ca.elevation);
      const end = new THREE.Vector3(...cb.position).setY(cb.elevation);
      const mid = new THREE.Vector3().lerpVectors(start, end, 0.5);
      const dir = new THREE.Vector3().subVectors(end, start);
      return {
        center: [mid.x, mid.y, mid.z] as [number, number, number],
        rotation: [0, -Math.atan2(dir.z, dir.x), 0] as [number, number, number],
        length: dir.length(),
      };
    });
  }, []);

  return (
    <>
      {segments.map((seg, i) => (
        <mesh key={i} position={seg.center} rotation={seg.rotation}>
          <boxGeometry args={[seg.length, 0.02, 0.04]} />
          <meshBasicMaterial color="#4ade80" transparent opacity={0.15} />
        </mesh>
      ))}
    </>
  );
}

/** Ground grid for Matrix aesthetic. */
function GroundGrid() {
  return <gridHelper args={[40, 40, "#1a2e1a", "#0a150a"]} position={[0, -0.2, 0]} />;
}

export default function CityScene({ data, selectedZone, onSelectZone }: CitySceneProps) {
  return (
    <Canvas
      camera={{ position: CAMERA_POSITION, fov: CAMERA_FOV }}
      style={{ background: "#050a05" }}
      gl={{ antialias: true, alpha: false }}
    >
      <Suspense fallback={null}>
        {/* Lighting */}
        <ambientLight intensity={0.3} />
        <pointLight position={[10, 20, 10]} intensity={0.8} color="#4ade80" />
        <pointLight position={[-10, 15, -10]} intensity={0.3} color="#22d3ee" />

        {/* Ground */}
        <GroundGrid />

        {/* Zone platforms */}
        {data.zones.map((zone) => (
          <ZonePlatform
            key={zone.slug}
            zone={zone}
            selected={selectedZone === zone.slug}
            onClick={() => onSelectZone(selectedZone === zone.slug ? null : zone.slug)}
          />
        ))}

        {/* Figurines */}
        <Figurines zones={data.zones} ratio={data.scale.figurine_ratio} />

        {/* Zone connection paths */}
        <ZonePaths />

        {/* Camera controls */}
        <OrbitControls
          enablePan
          enableZoom
          enableRotate
          minDistance={5}
          maxDistance={50}
          maxPolarAngle={Math.PI / 2.2}
        />
      </Suspense>
    </Canvas>
  );
}
