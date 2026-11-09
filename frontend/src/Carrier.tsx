// The carrier: a plane that brings the analysis drones to the city and takes them home.
// Driven by the event stream like everything else (ADR 0004): it flies in when a run starts, the
// drones launch from docks under its belly, and it leaves once the run is over and they're back aboard.

import { useFrame } from "@react-three/fiber";
import { useMemo, useRef } from "react";
import * as THREE from "three";
import type { CityLayout } from "./layout";

export const DOCKS = 6;

/** Shared with the drones and mutated every frame: where each dock is, and who is aboard. */
export interface CarrierState {
  /** in the air beside the city (arriving or holding); drones only show while it is, or while working */
  present: boolean;
  docks: THREE.Vector3[];
  aboard: Set<string>;
}

export function useCarrierState(): CarrierState {
  return useMemo(
    () => ({ present: false, docks: Array.from({ length: DOCKS }, () => new THREE.Vector3()), aboard: new Set() }),
    [],
  );
}

export type Phase = "away" | "arriving" | "holding" | "leaving";

/** Give up waiting for stragglers after this long, so a stuck drone can't keep the plane forever. */
export const LEAVE_TIMEOUT_S = 8;

/** The flight plan as a pure function: which phase follows, given what the event stream says. */
export function nextPhase(
  phase: Phase,
  s: { running: boolean; allAboard: boolean; idleFor: number; reachedOrbit: boolean; outOfSight: boolean },
): Phase {
  if (s.running) {
    if (phase === "away" || phase === "leaving") return "arriving";
    if (phase === "arriving" && s.reachedOrbit) return "holding";
    return phase;
  }
  if (phase === "arriving" || phase === "holding") {
    return s.allAboard || s.idleFor > LEAVE_TIMEOUT_S ? "leaving" : phase;
  }
  if (phase === "leaving" && s.outOfSight) return "away";
  return phase;
}

const ORBIT_R = 3.5;
const ORBIT_W = (2 * Math.PI) / 16; // one lap every 16 s
// Dock offsets under the belly, in the plane's own frame (+z is forward).
const DOCK_AT: [number, number, number][] = [
  [-1.5, -1.3, 0.8],
  [0, -1.3, 0.8],
  [1.5, -1.3, 0.8],
  [-1.5, -1.3, -1.2],
  [0, -1.3, -1.2],
  [1.5, -1.3, -1.2],
];

const BODY = "#c9d3e0";
const TRIM = "#5a6b80";
const ENGINE_GLOW = "#5ad1ff";

export function Carrier({
  layout,
  carrier,
  running,
  crew,
}: {
  layout: CityLayout;
  carrier: CarrierState;
  running: boolean;
  /** agent ids of the current run; the plane leaves only when all of them are aboard */
  crew: string[];
}) {
  const group = useRef<THREE.Group>(null);
  const strobe = useRef<THREE.Mesh>(null);
  const bay = useRef<THREE.MeshBasicMaterial>(null);
  const phase = useRef<Phase>("away");
  const idleFor = useRef(0);
  const pos = useRef(new THREE.Vector3());
  const vel = useRef(new THREE.Vector3());
  const tmp = useMemo(
    () => ({ orbit: new THREE.Vector3(), prev: new THREE.Vector3(), m: new THREE.Matrix4(), q: new THREE.Quaternion() }),
    [],
  );

  // Hold beside the city (left of the default camera), high enough to clear the tallest building.
  const route = useMemo(() => {
    const tallest = layout.placed.reduce((h, p) => Math.max(h, p.height), 0);
    const alt = Math.max(14, tallest + 9);
    const edge = layout.size / 2 + 8;
    return {
      hold: new THREE.Vector3(-edge, alt, 0),
      entry: new THREE.Vector3(-edge - layout.size * 1.6, alt + 14, -layout.size * 1.8),
      farAway: layout.size * 4,
    };
  }, [layout.size, layout.placed]);

  useFrame((state, rawDt) => {
    const g = group.current;
    if (!g) return;
    const dt = Math.min(rawDt, 0.1); // a backgrounded tab must not teleport the plane
    const t = state.clock.elapsedTime;
    const p = pos.current;

    idleFor.current = running ? 0 : idleFor.current + dt;
    tmp.orbit.set(
      route.hold.x + Math.sin(t * ORBIT_W) * ORBIT_R,
      route.hold.y + Math.sin(t * ORBIT_W * 2) * 0.4,
      route.hold.z + Math.cos(t * ORBIT_W) * ORBIT_R,
    );
    const before = phase.current;
    phase.current = nextPhase(before, {
      running,
      allAboard: crew.every((id) => carrier.aboard.has(id)),
      idleFor: idleFor.current,
      reachedOrbit: p.distanceTo(tmp.orbit) < 1.5,
      outOfSight: p.length() > route.farAway,
    });
    if (before === "away" && phase.current === "arriving") p.copy(route.entry);

    tmp.prev.copy(p);
    switch (phase.current) {
      case "arriving": // a long, decelerating approach
        p.lerp(tmp.orbit, 1 - Math.exp(-dt * 0.8));
        break;
      case "holding": // circle beside the city
        p.lerp(tmp.orbit, 1 - Math.exp(-dt * 3));
        break;
      case "leaving": {
        // Keep the current heading, speed up and climb away.
        const dir = vel.current.lengthSq() > 1e-6 ? vel.current.clone().normalize() : new THREE.Vector3(0, 0, -1);
        dir.y = Math.max(dir.y, 0.15);
        vel.current.addScaledVector(dir.normalize(), dt * 12);
        p.addScaledVector(vel.current, dt);
        break;
      }
    }
    if (phase.current !== "leaving" && dt > 0) vel.current.subVectors(p, tmp.prev).divideScalar(dt);

    g.visible = phase.current !== "away";
    carrier.present = phase.current === "arriving" || phase.current === "holding";
    g.position.copy(p);
    // Face the direction of travel, turning smoothly.
    if (vel.current.lengthSq() > 1e-4) {
      tmp.m.lookAt(tmp.prev.copy(p).add(vel.current), p, THREE.Object3D.DEFAULT_UP);
      tmp.q.setFromRotationMatrix(tmp.m);
      g.quaternion.slerp(tmp.q, 1 - Math.exp(-dt * 3));
    }
    g.updateMatrixWorld();
    DOCK_AT.forEach((d, i) => carrier.docks[i].set(d[0], d[1], d[2]).applyMatrix4(g.matrixWorld));

    if (strobe.current) strobe.current.visible = t % 1.2 < 0.08;
    // The bay glows while any drone is out.
    if (bay.current) bay.current.opacity = crew.some((id) => !carrier.aboard.has(id)) ? 0.55 : 0.15;
  });

  return (
    <group ref={group} visible={false}>
      {/* fuselage */}
      <mesh rotation-x={Math.PI / 2} castShadow>
        <capsuleGeometry args={[0.75, 5.2, 6, 14]} />
        <meshStandardMaterial color={BODY} metalness={0.45} roughness={0.35} />
      </mesh>
      {/* cockpit */}
      <mesh position={[0, 0.35, 2.6]} scale={[0.55, 0.4, 0.7]}>
        <sphereGeometry args={[1, 14, 10]} />
        <meshStandardMaterial color="#20334a" emissive="#5ad1ff" emissiveIntensity={0.35} roughness={0.15} />
      </mesh>
      {/* wings and tail */}
      <mesh position={[0, -0.1, 0.1]} castShadow>
        <boxGeometry args={[9, 0.16, 1.7]} />
        <meshStandardMaterial color={BODY} metalness={0.45} roughness={0.35} />
      </mesh>
      <mesh position={[0, 0.05, -2.9]}>
        <boxGeometry args={[3.2, 0.12, 0.9]} />
        <meshStandardMaterial color={TRIM} metalness={0.4} roughness={0.4} />
      </mesh>
      <mesh position={[0, 0.8, -2.9]}>
        <boxGeometry args={[0.14, 1.4, 1.1]} />
        <meshStandardMaterial color={TRIM} metalness={0.4} roughness={0.4} />
      </mesh>
      {/* engines, glowing at the back */}
      {[-2.4, 2.4].map((x) => (
        <group key={x} position={[x, -0.45, 0.2]}>
          <mesh rotation-x={Math.PI / 2}>
            <cylinderGeometry args={[0.32, 0.38, 1.6, 14]} />
            <meshStandardMaterial color={TRIM} metalness={0.6} roughness={0.3} />
          </mesh>
          <mesh position={[0, 0, -0.82]} rotation-x={Math.PI / 2}>
            <circleGeometry args={[0.3, 16]} />
            <meshBasicMaterial color={ENGINE_GLOW} side={THREE.DoubleSide} />
          </mesh>
        </group>
      ))}
      {/* drone bay under the belly */}
      <mesh position={[0, -0.78, -0.2]} rotation-x={Math.PI / 2}>
        <planeGeometry args={[3.6, 2.6]} />
        <meshBasicMaterial ref={bay} color={ENGINE_GLOW} transparent opacity={0.15} side={THREE.DoubleSide} depthWrite={false} />
      </mesh>
      {/* navigation lights: red left, green right, white tail strobe */}
      <mesh position={[-4.5, -0.1, 0.1]}>
        <sphereGeometry args={[0.12, 8, 8]} />
        <meshBasicMaterial color="#ff4d4d" />
      </mesh>
      <mesh position={[4.5, -0.1, 0.1]}>
        <sphereGeometry args={[0.12, 8, 8]} />
        <meshBasicMaterial color="#3ddc97" />
      </mesh>
      <mesh ref={strobe} position={[0, 1.55, -3.3]}>
        <sphereGeometry args={[0.14, 8, 8]} />
        <meshBasicMaterial color="#ffffff" />
      </mesh>
    </group>
  );
}
