import { Html, OrbitControls, QuadraticBezierLine } from "@react-three/drei";
import { Canvas, useFrame, useThree, type ThreeEvent } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { AgentsLayer } from "./Agents3D";
import { districtAccents, districtKind, KIND_LABEL } from "./districts";
import { Environment } from "./Environment";
import { layoutCity, riskColor, type CityLayout, type Placed } from "./layout";
import type { ImportNeighbors, Road } from "./types";
import type { AgentState, FindingMark, FixState, Spawned } from "./useCityStream";
import type { District } from "./types";

const RISE_MS = 650;
const ease = (t: number) => 1 - Math.pow(1 - t, 3);

interface Props {
  districts: District[];
  buildings: Spawned[];
  roads: Road[];
  maxRisk: number;
  hovered: string | null;
  selected: string | null;
  onHover: (path: string | null) => void;
  onSelect: (path: string | null) => void;
  focus: string | null;
  agents: Record<string, AgentState>;
  findings: Record<number, FindingMark>;
  fixes: Record<number, FixState>;
  analysisRunning: boolean;
  /** "Trace dependencies": arcs from the selected building to the files it imports and that import it. */
  trace: ImportNeighbors | null;
}

export function City(props: Props) {
  const layout = useMemo(() => layoutCity(props.districts, props.buildings), [props.districts, props.buildings]);
  const size = layout.size;
  return (
    <Canvas
      shadows="soft"
      dpr={[1, 2]}
      camera={{ position: [size * 0.8, size * 0.62, size * 0.8], fov: 42, near: 0.5, far: size * 14 }}
      onPointerMissed={() => props.onSelect(null)}
    >
      <color attach="background" args={["#0e131a"]} />
      <fog attach="fog" args={["#0e131a", size * 1.9, size * 4.5]} />
      {/* Key light (warm, casts soft shadows), sky/ground fill, and a cool rim from behind. */}
      <ambientLight intensity={0.18} />
      <hemisphereLight args={["#cfdcf0", "#1f2a22", 0.95]} />
      <directionalLight
        position={[size * 0.45, size * 1.05, size * 0.3]}
        intensity={2.3}
        color="#fff3e2"
        castShadow
        shadow-mapSize={[2048, 2048]}
        shadow-bias={-0.0004}
        shadow-normalBias={0.03}
        shadow-radius={4}
        shadow-camera-left={-size * 0.75}
        shadow-camera-right={size * 0.75}
        shadow-camera-top={size * 0.75}
        shadow-camera-bottom={-size * 0.75}
        shadow-camera-far={size * 3}
      />
      <directionalLight position={[-size * 0.6, size * 0.5, -size * 0.7]} intensity={0.55} color="#8fb4ff" />
      <CameraFit size={size} />
      <Ground layout={layout} />
      <Buildings {...props} placed={layout.placed} />
      <Roads layout={layout} roads={props.roads} />
      <AgentsLayer
        layout={layout}
        agents={props.agents}
        findings={props.findings}
        fixes={props.fixes}
        analysisRunning={props.analysisRunning}
        onSelect={(p) => props.onSelect(p)}
      />
      {props.trace && <TraceLayer layout={layout} trace={props.trace} />}
      <Tooltip layout={layout} path={props.hovered} />
      <OrbitControls
        makeDefault
        enableDamping
        dampingFactor={0.08}
        maxPolarAngle={Math.PI * 0.46}
        minDistance={4}
        maxDistance={size * 3}
      />
      <FocusRig layout={layout} path={props.focus} />
    </Canvas>
  );
}

function Ground({ layout }: { layout: CityLayout }) {
  const lots = [...layout.lots.values()];
  const accents = useMemo(() => districtAccents(lots.map((l) => l.district.name)), [lots]);
  return (
    <group>
      <Environment layout={layout} />
      {lots.map((lot) => (
        <group key={lot.district.name}>
          {lot.blocks.size > 1 &&
            [...lot.blocks.values()].map(
              (b) =>
                b.rect.w >= 4 &&
                b.rect.h >= 3 && (
                  <Html
                    key={b.folder}
                    position={[b.rect.x + b.rect.w / 2, 0.1, b.rect.y + b.rect.h - 0.35]}
                    center
                    zIndexRange={[9, 0]}
                    className="folder-label"
                  >
                    {b.label}/
                  </Html>
                ),
            )}
          <Html
            position={[lot.inner.x + lot.inner.w / 2, 0.05, lot.rect.y + lot.rect.h]}
            center
            zIndexRange={[10, 0]}
            className="district-label"
          >
            <span className="dl-dot" style={{ background: accents.get(lot.district.name) }} />
            {lot.district.name}
            <span className="dl-kind">{KIND_LABEL[districtKind(lot.district.name)]}</span>
          </Html>
        </group>
      ))}
    </group>
  );
}

const tmp = new THREE.Object3D();
const col = new THREE.Color();

function Buildings({
  placed,
  maxRisk,
  hovered,
  selected,
  onHover,
  onSelect,
  districts,
}: Props & { placed: Placed[] }) {
  const capacity = Math.max(
    1,
    placed.length,
    districts.reduce((s, d) => s + d.file_count, 0),
  );
  const ref = useRef<THREE.InstancedMesh>(null);
  const dirty = useRef(true);

  useEffect(() => {
    dirty.current = true;
  }, [placed]);

  // Colours change rarely (risk max, hover, selection); recompute them all at once.
  useEffect(() => {
    const mesh = ref.current;
    if (!mesh) return;
    placed.forEach((p, i) => {
      const b = p.building;
      if (b.path === selected) col.set("#ffffff");
      else {
        const [r, g, bl] = riskColor(b.risk, maxRisk, b.is_test);
        col.setRGB(r, g, bl, THREE.SRGBColorSpace);
        if (b.path === hovered) col.lerp(new THREE.Color("#ffffff"), 0.35);
      }
      mesh.setColorAt(i, col);
    });
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  }, [placed, maxRisk, hovered, selected, capacity]);

  // Matrices only change while something is still rising.
  useFrame(() => {
    const mesh = ref.current;
    if (!mesh) return;
    const now = performance.now();
    let rising = false;
    const spawned = placed as (Placed & { building: Spawned })[];
    if (!dirty.current && !spawned.some((p) => now - p.building.spawn < RISE_MS)) return;
    spawned.forEach((p, i) => {
      const t = Math.min(1, Math.max(0, (now - p.building.spawn) / RISE_MS));
      if (t < 1) rising = true;
      const h = Math.max(p.height * ease(t), 0.001);
      tmp.position.set(p.x, h / 2, p.z);
      tmp.scale.set(p.footprint, h, p.footprint);
      tmp.updateMatrix();
      mesh.setMatrixAt(i, tmp.matrix);
    });
    mesh.count = placed.length;
    mesh.instanceMatrix.needsUpdate = true;
    mesh.boundingSphere = null; // recomputed lazily for raycasting
    dirty.current = rising;
  });

  const pick = (e: ThreeEvent<PointerEvent | MouseEvent>) =>
    e.instanceId != null && e.instanceId < placed.length ? placed[e.instanceId].building.path : null;

  return (
    <instancedMesh
      key={capacity}
      ref={ref}
      args={[undefined, undefined, capacity]}
      castShadow
      receiveShadow
      frustumCulled={false}
      onPointerMove={(e) => {
        e.stopPropagation();
        const p = pick(e);
        if (p !== hovered) onHover(p);
      }}
      onPointerOut={() => onHover(null)}
      onClick={(e) => {
        e.stopPropagation();
        onSelect(pick(e));
      }}
    >
      <boxGeometry />
      <meshStandardMaterial roughness={0.75} metalness={0.05} />
    </instancedMesh>
  );
}

function Roads({ layout, roads }: { layout: CityLayout; roads: Road[] }) {
  return (
    <group>
      {roads.map((r) => {
        const a = layout.lots.get(r.src);
        const b = layout.lots.get(r.dst);
        if (!a || !b) return null;
        const ax = a.rect.x + a.rect.w / 2;
        const az = a.rect.y + a.rect.h / 2;
        const bx = b.rect.x + b.rect.w / 2;
        const bz = b.rect.y + b.rect.h / 2;
        const d = Math.hypot(bx - ax, bz - az);
        return (
          <QuadraticBezierLine
            key={`${r.src}->${r.dst}`}
            start={[ax, 0.3, az]}
            end={[bx, 0.3, bz]}
            mid={[(ax + bx) / 2, Math.max(6, d * 0.45), (az + bz) / 2]}
            color="#7fb0ff"
            lineWidth={1 + Math.log2(1 + r.imports)}
            transparent
            opacity={0.55}
          />
        );
      })}
    </group>
  );
}

function Tooltip({ layout, path }: { layout: CityLayout; path: string | null }) {
  const p = path ? layout.placed.find((q) => q.building.path === path) : undefined;
  if (!p) return null;
  const b = p.building;
  return (
    <Html position={[p.x, p.height + 0.6, p.z]} center className="tooltip">
      <div className="tooltip-path">{b.path}</div>
      <div className="tooltip-stats">
        CC {b.complexity_total} · {b.loc} LOC · churn {b.churn_90} · risk {b.risk.toFixed(1)}
      </div>
    </Html>
  );
}

/** Eases the orbit target (and camera, by the same offset) toward a focused building. */
function FocusRig({ layout, path }: { layout: CityLayout; path: string | null }) {
  const controls = useThree((s) => s.controls) as unknown as { target: THREE.Vector3; update: () => void } | null;
  const camera = useThree((s) => s.camera);
  const goal = useRef<THREE.Vector3 | null>(null);

  useEffect(() => {
    const p = path ? layout.placed.find((q) => q.building.path === path) : undefined;
    goal.current = p ? new THREE.Vector3(p.x, p.height / 2, p.z) : null;
    // layout changes as buildings stream in; only re-aim when the path changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path]);

  useFrame((_, dt) => {
    if (!controls || !goal.current) return;
    const k = 1 - Math.exp(-dt * 5);
    const step = goal.current.clone().sub(controls.target).multiplyScalar(k);
    controls.target.add(step);
    camera.position.add(step);
    controls.update();
    if (goal.current.distanceTo(controls.target) < 0.05) goal.current = null;
  });
  return null;
}

/** Frame the whole city to fill the viewport, once per city (after that the camera is yours). */
function CameraFit({ size }: { size: number }) {
  const camera = useThree((s) => s.camera) as THREE.PerspectiveCamera;
  const aspect = useThree((s) => s.size.width / Math.max(1, s.size.height));
  const controls = useThree((s) => s.controls) as unknown as { target: THREE.Vector3; update: () => void } | null;
  const fitted = useRef<number | null>(null);
  useEffect(() => {
    if (fitted.current === size) return;
    fitted.current = size;
    // Radius of the city plus its ring road; fit it to the narrower of the two view angles.
    const radius = size * 0.6 + 2;
    const vfov = (camera.fov * Math.PI) / 180;
    const hfov = 2 * Math.atan(Math.tan(vfov / 2) * aspect);
    const dist = radius / Math.sin(Math.min(vfov, hfov) / 2);
    const dir = new THREE.Vector3(1, 0.82, 1).normalize();
    camera.position.copy(dir.multiplyScalar(dist * 0.92));
    camera.far = Math.max(camera.far, dist * 6);
    camera.updateProjectionMatrix();
    if (controls) {
      controls.target.set(0, 0, 0);
      controls.update();
    } else camera.lookAt(0, 0, 0);
  }, [size, aspect, camera, controls]);
  return null;
}

const OUT_COLOR = "#5ad1ff"; // files this one imports
const IN_COLOR = "#ffb35a"; // files that import this one

function TraceLayer({ layout, trace }: { layout: CityLayout; trace: ImportNeighbors }) {
  const byPath = useMemo(() => new Map(layout.placed.map((p) => [p.building.path, p])), [layout.placed]);
  const src = byPath.get(trace.path);
  if (!src) return null;
  const links = [
    ...trace.imports.map((p) => ({ to: byPath.get(p), color: OUT_COLOR })),
    ...trace.imported_by.map((p) => ({ to: byPath.get(p), color: IN_COLOR })),
  ].filter((l): l is { to: Placed; color: string } => !!l.to && l.to !== src);
  return (
    <group>
      {links.map(({ to, color }, i) => {
        const d = Math.hypot(to.x - src.x, to.z - src.z);
        const top = Math.max(src.height, to.height) + 2 + d * 0.35;
        return (
          <group key={i}>
            <QuadraticBezierLine
              start={[src.x, src.height + 0.2, src.z]}
              end={[to.x, to.height + 0.2, to.z]}
              mid={[(src.x + to.x) / 2, top, (src.z + to.z) / 2]}
              color={color}
              lineWidth={2.2}
              transparent
              opacity={0.9}
            />
            <RoofRing placed={to} color={color} />
          </group>
        );
      })}
      <RoofRing placed={src} color="#ffffff" />
    </group>
  );
}

function RoofRing({ placed, color }: { placed: Placed; color: string }) {
  const ref = useRef<THREE.Mesh>(null);
  useFrame((state) => {
    if (ref.current) ref.current.scale.setScalar(1 + Math.sin(state.clock.elapsedTime * 3) * 0.08);
  });
  return (
    <mesh ref={ref} position={[placed.x, placed.height + 0.05, placed.z]} rotation-x={-Math.PI / 2}>
      <ringGeometry args={[placed.footprint * 0.62, placed.footprint * 0.78, 32]} />
      <meshBasicMaterial color={color} transparent opacity={0.9} side={THREE.DoubleSide} depthWrite={false} />
    </mesh>
  );
}
