// Cars driving the city's streets. One InstancedMesh per car part, so 60 cars are 4 draw calls.
// Decorative only: non-interactive (raycast disabled) and independent of the event stream.

import { useFrame } from "@react-three/fiber";
import { useMemo, useRef } from "react";
import * as THREE from "three";
import type { CityLayout } from "./layout";
import { ROAD_Y, rng, streetCenterlines } from "./scenery";
import { advance, carPose, roadGraph, spawnCars } from "./traffic";

const LANE = 0.42; // offset from the centerline; streets are 2.6 wide, so this is mid right-hand lane
const BOX = new THREE.BoxGeometry(1, 1, 1);

// Car parts in car space (+x is forward), as [position, scale].
const PARTS = {
  body: [new THREE.Vector3(0, 0.2, 0), new THREE.Vector3(0.95, 0.26, 0.46)],
  cabin: [new THREE.Vector3(-0.07, 0.42, 0), new THREE.Vector3(0.5, 0.2, 0.4)],
  head: [new THREE.Vector3(0.48, 0.22, 0), new THREE.Vector3(0.03, 0.07, 0.36)],
  tail: [new THREE.Vector3(-0.48, 0.22, 0), new THREE.Vector3(0.03, 0.07, 0.38)],
} as const;
type Part = keyof typeof PARTS;
const LOCAL = Object.fromEntries(
  Object.entries(PARTS).map(([k, [p, s]]) => [k, new THREE.Matrix4().compose(p, new THREE.Quaternion(), s)]),
) as Record<Part, THREE.Matrix4>;

const carM = new THREE.Matrix4();
const partM = new THREE.Matrix4();
const q = new THREE.Quaternion();
const up = new THREE.Vector3(0, 1, 0);
const one = new THREE.Vector3(1, 1, 1);
const at = new THREE.Vector3();

export function Traffic({ layout }: { layout: CityLayout }) {
  // The layout object changes with every building that rises during ingest, but the streets only
  // change with the districts: key the traffic on the lots so the cars don't reset mid-build.
  const streets = [...layout.lots.values()].map((l) => `${l.rect.x},${l.rect.y},${l.rect.w},${l.rect.h}`).join("|");
  const sim = useMemo(() => {
    const graph = roadGraph(streetCenterlines(layout));
    const rand = rng(layout.lots.size * 7919 + Math.round(layout.size * 100));
    const cars = spawnCars(graph, rand);
    // Rendered pose trails the logical one slightly, which rounds off the corner at each junction.
    const shown = cars.map((c) => {
      const p = carPose(graph, c, LANE);
      return { x: p.x, z: p.z, yaw: Math.atan2(-p.dz, p.dx) };
    });
    return { graph, rand, cars, shown, id: streets };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streets]);

  const refs = {
    body: useRef<THREE.InstancedMesh>(null),
    cabin: useRef<THREE.InstancedMesh>(null),
    head: useRef<THREE.InstancedMesh>(null),
    tail: useRef<THREE.InstancedMesh>(null),
  };
  const painted = useRef<THREE.InstancedMesh | null>(null);

  useFrame((_, rawDt) => {
    const dt = Math.min(rawDt, 0.1);
    const { graph, rand, cars, shown } = sim;
    const body = refs.body.current;
    if (!body) return;
    if (painted.current !== body) {
      // Per-car paint, set once per mesh (a new layout remounts the meshes).
      const c = new THREE.Color();
      cars.forEach((car, i) => body.setColorAt(i, c.set(car.color)));
      if (body.instanceColor) body.instanceColor.needsUpdate = true;
      painted.current = body;
    }
    const follow = 1 - Math.exp(-dt * 10);
    cars.forEach((car, i) => {
      advance(graph, car, car.speed * dt, rand);
      const p = carPose(graph, car, LANE);
      const v = shown[i];
      v.x += (p.x - v.x) * follow;
      v.z += (p.z - v.z) * follow;
      const yaw = Math.atan2(-p.dz, p.dx);
      let turn = yaw - v.yaw;
      turn = Math.atan2(Math.sin(turn), Math.cos(turn)); // shortest way round
      v.yaw += turn * follow;
      carM.compose(at.set(v.x, ROAD_Y, v.z), q.setFromAxisAngle(up, v.yaw), one);
      for (const part of Object.keys(refs) as Part[]) {
        refs[part].current?.setMatrixAt(i, partM.multiplyMatrices(carM, LOCAL[part]));
      }
    });
    for (const part of Object.keys(refs) as Part[]) {
      const m = refs[part].current;
      if (m) m.instanceMatrix.needsUpdate = true;
    }
  });

  const n = sim.cars.length;
  if (!n) return null;
  return (
    // New streets remount the meshes, so they're sized and painted for the new cars.
    <group key={sim.id}>
      <instancedMesh ref={refs.body} args={[BOX, undefined, n]} castShadow raycast={() => null} frustumCulled={false}>
        <meshStandardMaterial color="#ffffff" metalness={0.35} roughness={0.45} />
      </instancedMesh>
      <instancedMesh ref={refs.cabin} args={[BOX, undefined, n]} castShadow raycast={() => null} frustumCulled={false}>
        <meshStandardMaterial color="#1c2430" metalness={0.5} roughness={0.2} />
      </instancedMesh>
      <instancedMesh ref={refs.head} args={[BOX, undefined, n]} raycast={() => null} frustumCulled={false}>
        <meshBasicMaterial color="#fff2c4" />
      </instancedMesh>
      <instancedMesh ref={refs.tail} args={[BOX, undefined, n]} raycast={() => null} frustumCulled={false}>
        <meshBasicMaterial color="#ff4040" />
      </instancedMesh>
    </group>
  );
}
