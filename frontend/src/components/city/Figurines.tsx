import { useRef, useMemo, useEffect } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";
import type { CityZone, CityAgent } from "../../types";
import { ACTIVITY_COLORS, ZONE_CONFIG, ZONE_BASE_SIZE, FIGURINE_HEIGHT } from "./constants";

interface FigurinesProps {
  zones: CityZone[];
  ratio: number;
}

/** Simple hash function for deterministic scatter placement. */
function hashCode(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) {
    h = (Math.imul(31, h) + s.charCodeAt(i)) | 0;
  }
  return Math.abs(h);
}

/** Map activity to a Color object. */
function activityColor(activity: string): THREE.Color {
  const hex = ACTIVITY_COLORS[activity] || ACTIVITY_COLORS.idle;
  return new THREE.Color(hex);
}

interface FigurineInstance {
  position: THREE.Vector3;
  color: THREE.Color;
  activity: string;
  agentId: string;
}

export default function Figurines({ zones, ratio }: FigurinesProps) {
  const meshRef = useRef<THREE.InstancedMesh>(null);
  const dummyRef = useRef(new THREE.Object3D());

  // Build instance list from zone agents
  const instances: FigurineInstance[] = useMemo(() => {
    const result: FigurineInstance[] = [];

    for (const zone of zones) {
      const config = ZONE_CONFIG[zone.slug] || ZONE_CONFIG.outskirts;
      const [cx, _cy, cz] = config.position;
      const agents = zone.agents.filter((a) => a.activity !== "inactive");

      // Determine how many figurines to show in this zone
      const figurineCount =
        ratio <= 1 ? agents.length : Math.max(1, Math.round(agents.length / ratio));

      // Select representative agents (take first N after sort by ID for stability)
      const reps = agents.slice(0, figurineCount);

      for (let i = 0; i < reps.length; i++) {
        const agent: CityAgent = reps[i];
        const hash = hashCode(agent.id);

        // Scatter within zone bounds using deterministic hash
        const spreadX = ZONE_BASE_SIZE * 0.35;
        const spreadZ = ZONE_BASE_SIZE * 0.35;
        const fx = cx + ((hash % 1000) / 500 - 1) * spreadX;
        const fz = cz + (((hash >> 10) % 1000) / 500 - 1) * spreadZ;
        const fy = config.elevation + FIGURINE_HEIGHT / 2 + 0.02;

        result.push({
          position: new THREE.Vector3(fx, fy, fz),
          color: activityColor(agent.activity),
          activity: agent.activity,
          agentId: agent.id,
        });
      }
    }
    return result;
  }, [zones, ratio]);

  const count = instances.length;

  // Geometry: cylinder body for each figurine (disposed on unmount)
  const geometry = useMemo(
    () => new THREE.CylinderGeometry(0.03, 0.04, FIGURINE_HEIGHT * 0.7, 6),
    [],
  );
  useEffect(() => {
    return () => geometry.dispose();
  }, [geometry]);

  // Set initial transforms and colors
  useEffect(() => {
    if (!meshRef.current) return;
    for (let i = 0; i < count; i++) {
      const inst = instances[i];
      dummyRef.current.position.copy(inst.position);
      dummyRef.current.updateMatrix();
      meshRef.current.setMatrixAt(i, dummyRef.current.matrix);
      meshRef.current.setColorAt(i, inst.color);
    }
    meshRef.current.instanceMatrix.needsUpdate = true;
    if (meshRef.current.instanceColor) {
      meshRef.current.instanceColor.needsUpdate = true;
    }
  }, [instances, count]);

  // Micro-animations
  const timeRef = useRef(0);
  useFrame((_state, delta) => {
    if (!meshRef.current || count === 0) return;
    timeRef.current += delta;
    const t = timeRef.current;

    for (let i = 0; i < count; i++) {
      const inst = instances[i];
      dummyRef.current.position.copy(inst.position);

      // Activity-based animation
      switch (inst.activity) {
        case "working":
        case "employed":
          // Bobbing
          dummyRef.current.position.y += Math.sin(t * 4 + i) * 0.03;
          break;
        case "gathering":
          // Lean forward
          dummyRef.current.rotation.x = Math.sin(t * 2 + i) * 0.15;
          break;
        case "trading":
        case "negotiating":
          // Slow rotate
          dummyRef.current.rotation.y = t * 0.5 + i;
          break;
        case "idle":
          // Gentle oscillation (sin-based, stays near origin)
          dummyRef.current.position.x += Math.sin(t * 0.3 + i * 2) * 0.001;
          dummyRef.current.position.z += Math.cos(t * 0.3 + i * 3) * 0.001;
          break;
        case "jailed":
          // Red pulse via slight scale
          dummyRef.current.scale.setScalar(1 + Math.sin(t * 3) * 0.05);
          break;
        default:
          dummyRef.current.rotation.set(0, 0, 0);
          dummyRef.current.scale.setScalar(1);
      }

      dummyRef.current.updateMatrix();
      meshRef.current.setMatrixAt(i, dummyRef.current.matrix);

      // Reset rotation for next iteration
      dummyRef.current.rotation.set(0, 0, 0);
      dummyRef.current.scale.setScalar(1);
    }
    meshRef.current.instanceMatrix.needsUpdate = true;
  });

  if (count === 0) return null;

  return (
    <>
      <instancedMesh ref={meshRef} args={[geometry, undefined, count]} frustumCulled={false}>
        <meshStandardMaterial
          emissive="#4ade80"
          emissiveIntensity={0.6}
          color="#1a3a1a"
          roughness={0.4}
          metalness={0.3}
          transparent
          opacity={0.9}
        />
      </instancedMesh>
      {/* Figurine heads as a second instanced mesh */}
      <FigurineHeads instances={instances} />
    </>
  );
}

/** Separate instanced mesh for figurine heads (spheres). */
function FigurineHeads({ instances }: { instances: FigurineInstance[] }) {
  const meshRef = useRef<THREE.InstancedMesh>(null);
  const dummyRef = useRef(new THREE.Object3D());
  const geometry = useMemo(() => new THREE.SphereGeometry(0.04, 8, 6), []);
  const count = instances.length;

  useEffect(() => {
    return () => geometry.dispose();
  }, [geometry]);

  useEffect(() => {
    if (!meshRef.current) return;
    for (let i = 0; i < count; i++) {
      const inst = instances[i];
      dummyRef.current.position.set(
        inst.position.x,
        inst.position.y + FIGURINE_HEIGHT * 0.4,
        inst.position.z,
      );
      dummyRef.current.updateMatrix();
      meshRef.current.setMatrixAt(i, dummyRef.current.matrix);
      meshRef.current.setColorAt(i, inst.color);
    }
    meshRef.current.instanceMatrix.needsUpdate = true;
    if (meshRef.current.instanceColor) {
      meshRef.current.instanceColor.needsUpdate = true;
    }
  }, [instances, count]);

  if (count === 0) return null;

  return (
    <instancedMesh ref={meshRef} args={[geometry, undefined, count]} frustumCulled={false}>
      <meshStandardMaterial
        emissive="#4ade80"
        emissiveIntensity={0.8}
        color="#1a3a1a"
        roughness={0.3}
        metalness={0.2}
        transparent
        opacity={0.9}
      />
    </instancedMesh>
  );
}
