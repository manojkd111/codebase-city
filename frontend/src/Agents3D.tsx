// Drones, finding beacons and fix scaffolding. Everything here is driven by the event stream:
// a drone's target is simply the building for the file its agent last touched.

import { Html } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { useMemo, useRef } from "react";
import * as THREE from "three";
import type { CityLayout, Placed } from "./layout";
import type { Role, Severity } from "./types";
import type { AgentState, FindingMark, FixState } from "./useCityStream";

export const ROLE_COLOR: Record<Role, string> = {
  security: "#ff5d73",
  tests: "#3ddc97",
  debt: "#b48cff",
};
export const FIXER_COLOR = "#5ad1ff";
export const SEVERITY_COLOR: Record<Severity, string> = {
  critical: "#ff3b3b",
  high: "#ff8a3d",
  medium: "#ffd23f",
  low: "#7fb0ff",
};
const SEVERITY_RANK: Record<Severity, number> = { critical: 0, high: 1, medium: 2, low: 3 };

function usePlacedByPath(layout: CityLayout) {
  return useMemo(() => new Map(layout.placed.map((p) => [p.building.path, p])), [layout.placed]);
}

function hangar(layout: CityLayout, i: number): THREE.Vector3 {
  const h = layout.size / 2 + 4;
  return new THREE.Vector3(-h + i * 3, 10, -h);
}

interface Props {
  layout: CityLayout;
  agents: Record<string, AgentState>;
  findings: Record<number, FindingMark>;
  fixes: Record<number, FixState>;
  onSelect: (path: string) => void;
}

export function AgentsLayer({ layout, agents, findings, fixes, onSelect }: Props) {
  const byPath = usePlacedByPath(layout);
  const list = Object.values(agents);
  const activeFixes = Object.values(fixes).filter((f) => f.status === "running");
  return (
    <group>
      {list.map((a, i) => (
        <Drone
          key={a.id}
          color={ROLE_COLOR[a.role]}
          label={a.role}
          target={a.status === "working" && a.path ? byPath.get(a.path) : undefined}
          home={hangar(layout, i)}
          active={a.status === "working"}
        />
      ))}
      {activeFixes.map((f, i) => {
        const file = findings[f.findingId]?.file;
        const p = file ? byPath.get(file) : undefined;
        return (
          <group key={`fix-${f.id}`}>
            {p && <Scaffold placed={p} />}
            {p && <FixGlow placed={p} />}
            <Drone
              color={FIXER_COLOR}
              label={`fixer${f.step ? ` · ${f.step}` : ""}`}
              target={p}
              home={hangar(layout, list.length + i)}
              active
            />
          </group>
        );
      })}
      <Beacons byPath={byPath} findings={findings} onSelect={onSelect} />
    </group>
  );
}

function Drone({
  color,
  label,
  target,
  home,
  active,
}: {
  color: string;
  label: string;
  target: Placed | undefined;
  home: THREE.Vector3;
  active: boolean;
}) {
  const group = useRef<THREE.Group>(null);
  const body = useRef<THREE.Mesh>(null);
  const beam = useRef<THREE.Mesh>(null);
  const pos = useRef(home.clone());
  const goal = useMemo(() => new THREE.Vector3(), []);

  useFrame((state, dt) => {
    const g = group.current;
    if (!g) return;
    const t = state.clock.elapsedTime;
    if (target) goal.set(target.x, target.height + 2.6, target.z);
    else goal.copy(home);
    // Critically damped approach: fast when far, settles without overshoot.
    pos.current.lerp(goal, 1 - Math.exp(-dt * 2.2));
    g.position.set(pos.current.x, pos.current.y + Math.sin(t * 2.4) * 0.18, pos.current.z);
    if (body.current) body.current.rotation.y += dt * 2.2;
    if (beam.current) {
      const arrived = !!target && pos.current.distanceTo(goal) < 0.6;
      beam.current.visible = arrived;
      if (arrived) {
        const len = Math.max(0.1, pos.current.y - target!.height);
        beam.current.scale.set(1, len, 1);
        beam.current.position.y = -len / 2;
        (beam.current.material as THREE.MeshBasicMaterial).opacity = 0.25 + Math.sin(t * 6) * 0.1;
      }
    }
  });

  return (
    <group ref={group}>
      <mesh ref={body} scale={active ? 1 : 0.7}>
        <octahedronGeometry args={[0.65, 0]} />
        <meshStandardMaterial
          color={color}
          emissive={color}
          emissiveIntensity={active ? 1.4 : 0.3}
          roughness={0.3}
          transparent
          opacity={active ? 1 : 0.55}
        />
      </mesh>
      <mesh ref={beam} visible={false}>
        <cylinderGeometry args={[0.08, 0.5, 1, 12, 1, true]} />
        <meshBasicMaterial color={color} transparent opacity={0.3} depthWrite={false} side={THREE.DoubleSide} />
      </mesh>
      {active && (
        <Html position={[0, 1.1, 0]} center className="drone-label" zIndexRange={[20, 0]}>
          <span style={{ color }}>{label}</span>
        </Html>
      )}
    </group>
  );
}

/** A pulsing shell around the building being fixed. An overlay: the building itself is untouched. */
function FixGlow({ placed }: { placed: Placed }) {
  const mat = useRef<THREE.MeshBasicMaterial>(null);
  useFrame((state) => {
    if (mat.current) mat.current.opacity = 0.16 + 0.12 * (0.5 + 0.5 * Math.sin(state.clock.elapsedTime * 3.2));
  });
  const pad = 0.18;
  return (
    <mesh position={[placed.x, placed.height / 2 + pad / 2, placed.z]} raycast={() => null}>
      <boxGeometry args={[placed.footprint + pad * 2, placed.height + pad, placed.footprint + pad * 2]} />
      <meshBasicMaterial
        ref={mat}
        color={FIXER_COLOR}
        transparent
        depthWrite={false}
        blending={THREE.AdditiveBlending}
      />
    </mesh>
  );
}

function Scaffold({ placed }: { placed: Placed }) {
  const ref = useRef<THREE.Mesh>(null);
  useFrame((state) => {
    if (!ref.current) return;
    const t = state.clock.elapsedTime;
    ref.current.rotation.z = t * 1.5;
    ref.current.position.y = placed.height * (0.5 + 0.45 * Math.sin(t * 1.2));
  });
  const r = placed.footprint * 0.85;
  return (
    <mesh ref={ref} position={[placed.x, placed.height / 2, placed.z]} rotation-x={Math.PI / 2}>
      <torusGeometry args={[r, 0.07, 8, 32]} />
      <meshBasicMaterial color={FIXER_COLOR} />
    </mesh>
  );
}

function Beacons({
  byPath,
  findings,
  onSelect,
}: {
  byPath: Map<string, Placed>;
  findings: Record<number, FindingMark>;
  onSelect: (path: string) => void;
}) {
  // One beacon per file: the colour of its worst live finding, or green once everything is fixed.
  const perFile = useMemo(() => {
    const m = new Map<string, { color: string; count: number }>();
    const worst = new Map<string, Severity>();
    const live = new Map<string, number>();
    const fixed = new Set<string>();
    for (const f of Object.values(findings)) {
      if (f.status === "rejected") continue;
      if (f.status === "fixed") {
        fixed.add(f.file);
        continue;
      }
      live.set(f.file, (live.get(f.file) ?? 0) + 1);
      const w = worst.get(f.file);
      if (!w || SEVERITY_RANK[f.severity] < SEVERITY_RANK[w]) worst.set(f.file, f.severity);
    }
    for (const [file, sev] of worst) m.set(file, { color: SEVERITY_COLOR[sev], count: live.get(file)! });
    for (const file of fixed) if (!m.has(file)) m.set(file, { color: "#3ddc97", count: 0 });
    return m;
  }, [findings]);

  return (
    <group>
      {[...perFile].map(([file, b]) => {
        const p = byPath.get(file);
        return p ? <Beacon key={file} placed={p} color={b.color} count={b.count} onClick={() => onSelect(file)} /> : null;
      })}
    </group>
  );
}

function Beacon({ placed, color, count, onClick }: { placed: Placed; color: string; count: number; onClick: () => void }) {
  const ref = useRef<THREE.Group>(null);
  const phase = useMemo(() => Math.random() * Math.PI * 2, []);
  useFrame((state, dt) => {
    if (!ref.current) return;
    ref.current.rotation.y += dt * 1.6;
    ref.current.position.y = placed.height + 1.3 + Math.sin(state.clock.elapsedTime * 2 + phase) * 0.15;
  });
  return (
    <group position={[placed.x, 0, placed.z]}>
      <group ref={ref}>
        <mesh
          onClick={(e) => {
            e.stopPropagation();
            onClick();
          }}
          onPointerOver={() => (document.body.style.cursor = "pointer")}
          onPointerOut={() => (document.body.style.cursor = "")}
        >
          <octahedronGeometry args={[0.45, 0]} />
          <meshStandardMaterial color={color} emissive={color} emissiveIntensity={1.2} />
        </mesh>
      </group>
      {count > 1 && (
        <Html position={[0, placed.height + 2.3, 0]} center className="beacon-count" zIndexRange={[15, 0]}>
          {count}
        </Html>
      )}
    </group>
  );
}
