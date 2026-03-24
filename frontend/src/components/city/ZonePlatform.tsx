import { useRef, useState } from "react";
import { useFrame } from "@react-three/fiber";
import { Text } from "@react-three/drei";
import type * as THREE from "three";
import type { CityZone } from "../../types";
import { ZONE_CONFIG, ZONE_BASE_SIZE } from "./constants";
import { fmt } from "../formatters";

interface ZonePlatformProps {
  zone: CityZone;
  selected: boolean;
  onClick: () => void;
}

function zoneScale(gdpShare: number): number {
  const BASE = 0.4;
  const EQUAL_SHARE = 1.0 / 5;
  const gdpScaleFactor = gdpShare / EQUAL_SHARE;
  return BASE + (1 - BASE) * gdpScaleFactor;
}

export default function ZonePlatform({ zone, selected, onClick }: ZonePlatformProps) {
  const meshRef = useRef<THREE.Mesh>(null);
  const [hovered, setHovered] = useState(false);
  const config = ZONE_CONFIG[zone.slug] || ZONE_CONFIG.outskirts;

  const scale = zoneScale(zone.gdp_share);
  const size = ZONE_BASE_SIZE * scale;
  const targetY = config.elevation;

  // Smooth scale transition
  useFrame(() => {
    if (!meshRef.current) return;
    const s = meshRef.current.scale;
    s.x += (size - s.x) * 0.05;
    s.z += (size - s.z) * 0.05;
    meshRef.current.position.y += (targetY - meshRef.current.position.y) * 0.05;
  });

  const borderColor = selected ? "#4ade80" : hovered ? "#2dd4bf" : "#334155";

  return (
    <group position={config.position}>
      {/* Platform */}
      <mesh
        ref={meshRef}
        position={[0, targetY, 0]}
        rotation={[-Math.PI / 2, 0, 0]}
        scale={[size, size, 1]}
        onClick={(e) => {
          e.stopPropagation();
          onClick();
        }}
        onPointerOver={() => setHovered(true)}
        onPointerOut={() => setHovered(false)}
      >
        <planeGeometry args={[1, 1]} />
        <meshStandardMaterial
          color={config.color}
          emissive={borderColor}
          emissiveIntensity={selected ? 0.3 : hovered ? 0.15 : 0.05}
          roughness={0.8}
        />
      </mesh>

      {/* Zone edge border */}
      <mesh
        position={[0, targetY - 0.01, 0]}
        rotation={[-Math.PI / 2, 0, 0]}
        scale={[size + 0.1, size + 0.1, 1]}
      >
        <planeGeometry args={[1, 1]} />
        <meshBasicMaterial color={borderColor} transparent opacity={0.3} />
      </mesh>

      {/* Zone label */}
      <Text
        position={[0, targetY + 0.8, 0]}
        fontSize={0.3}
        color="#e2e8f0"
        anchorX="center"
        anchorY="middle"
      >
        {zone.name.toUpperCase()}
      </Text>
      <Text
        position={[0, targetY + 0.5, 0]}
        fontSize={0.18}
        color="#94a3b8"
        anchorX="center"
        anchorY="middle"
      >
        {`GDP: $${fmt(zone.gdp_6h)} (${Math.round(zone.gdp_share * 100)}%)`}
      </Text>
      <Text
        position={[0, targetY + 0.3, 0]}
        fontSize={0.15}
        color="#64748b"
        anchorX="center"
        anchorY="middle"
      >
        {`Pop: ${zone.population} | Biz: ${zone.businesses.total}`}
      </Text>
    </group>
  );
}
