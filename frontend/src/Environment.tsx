// Ground layer: grass, ring road, streets with lane markings, traffic, sidewalks, plazas, gardens, trees, lamps.
// Purely decorative and non-interactive (no pointer handlers, so it never blocks building clicks).
// Everything repeated is one InstancedMesh, so a big city still costs a handful of draw calls.

import { useLayoutEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import {
  beltSize,
  gardenCells,
  lampPosts,
  laneDashes,
  ringRoadSize,
  ROAD_Y,
  sidewalkWidth,
  streetCenterlines,
  trees,
} from "./scenery";
import { districtAccents } from "./districts";
import type { CityLayout } from "./layout";
import { Traffic } from "./TrafficLayer";

interface Item {
  x: number;
  y: number;
  z: number;
  sx: number;
  sy: number;
  sz: number;
  ry?: number;
  color?: string;
}

const dummy = new THREE.Object3D();
const tint = new THREE.Color();

function Instanced({
  items,
  geometry,
  color,
  emissive,
  roughness = 0.9,
  castShadow = false,
  receiveShadow = true,
}: {
  items: Item[];
  geometry: THREE.BufferGeometry;
  color: string;
  emissive?: string;
  roughness?: number;
  castShadow?: boolean;
  receiveShadow?: boolean;
}) {
  const ref = useRef<THREE.InstancedMesh>(null);
  useLayoutEffect(() => {
    const mesh = ref.current;
    if (!mesh) return;
    items.forEach((it, i) => {
      dummy.position.set(it.x, it.y, it.z);
      dummy.rotation.set(0, it.ry ?? 0, 0);
      dummy.scale.set(it.sx, it.sy, it.sz);
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);
      if (it.color) mesh.setColorAt(i, tint.set(it.color));
    });
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    mesh.computeBoundingSphere();
  }, [items]);
  if (!items.length) return null;
  return (
    <instancedMesh
      key={items.length}
      ref={ref}
      args={[geometry, undefined, items.length]}
      castShadow={castShadow}
      receiveShadow={receiveShadow}
      raycast={() => null}
    >
      <meshStandardMaterial
        color={items[0].color ? "#ffffff" : color}
        emissive={emissive ?? "#000000"}
        emissiveIntensity={emissive ? 1.6 : 0}
        roughness={roughness}
      />
    </instancedMesh>
  );
}

const BOX = new THREE.BoxGeometry(1, 1, 1);
const PLANE = new THREE.PlaneGeometry(1, 1).rotateX(-Math.PI / 2);
const TRUNK = new THREE.CylinderGeometry(0.07, 0.1, 1, 6).translate(0, 0.5, 0);
const CANOPY = new THREE.IcosahedronGeometry(0.5, 0);
const POLE = new THREE.CylinderGeometry(0.035, 0.05, 1, 6).translate(0, 0.5, 0);
const BULB = new THREE.SphereGeometry(0.11, 8, 6);
const BUSH = new THREE.IcosahedronGeometry(0.5, 0);

const GREENS = ["#2f6b45", "#3a7a4a", "#2b5e3f", "#4a7f3e", "#36704f"];

export function Environment({ layout }: { layout: CityLayout }) {
  const scene = useMemo(() => {
    const lots = [...layout.lots.values()];
    const segments = streetCenterlines(layout);
    const gardens = gardenCells(layout);
    const forest = trees(layout, gardens);

    const sidewalks: Item[] = lots.map((lot) => {
      const sw = sidewalkWidth(lot);
      const { x, y, w, h } = lot.inner;
      return { x: x + w / 2, y: (ROAD_Y - 0.03) / 2, z: y + h / 2, sx: w + 2 * sw, sy: -ROAD_Y + 0.03 - 0.04, sz: h + 2 * sw };
    });
    // Curbs: a slightly lighter, slightly taller rim on the sidewalk's outer edge.
    const accents = districtAccents(lots.map((l) => l.district.name));
    const curbs: Item[] = lots.flatMap((lot) => {
      const sw = sidewalkWidth(lot);
      const color = accents.get(lot.district.name);
      const { x, y, w, h } = lot.inner;
      const t = 0.12;
      const yy = (ROAD_Y + -0.02) / 2;
      const sy = -0.02 - ROAD_Y;
      return [
        { x: x + w / 2, y: yy, z: y - sw, sx: w + 2 * sw, sy, sz: t, color },
        { x: x + w / 2, y: yy, z: y + h + sw, sx: w + 2 * sw, sy, sz: t, color },
        { x: x - sw, y: yy, z: y + h / 2, sx: t, sy, sz: h + 2 * sw, color },
        { x: x + w + sw, y: yy, z: y + h / 2, sx: t, sy, sz: h + 2 * sw, color },
      ];
    });
    const plazas: Item[] = lots.map((lot) => ({
      x: lot.inner.x + lot.inner.w / 2,
      y: -0.02,
      z: lot.inner.y + lot.inner.h / 2,
      sx: lot.inner.w,
      sy: 0.04,
      sz: lot.inner.h,
    }));
    // Folder blocks inside a district: paving slightly lighter than the alleys between them.
    const paving: Item[] = lots.flatMap((lot) =>
      lot.blocks.size > 1
        ? [...lot.blocks.values()].map((b) => ({
            x: b.rect.x + b.rect.w / 2,
            y: 0.001,
            z: b.rect.y + b.rect.h / 2,
            sx: b.rect.w,
            sy: 1,
            sz: b.rect.h,
          }))
        : [],
    );
    const lawns: Item[] = gardens.map((g) => ({
      x: g.x + g.w / 2,
      y: 0.03,
      z: g.y + g.h / 2,
      sx: g.w * 0.86,
      sy: 0.06,
      sz: g.h * 0.86,
    }));
    const bushes: Item[] = gardens.flatMap((g, i) => {
      const s = Math.min(g.w, g.h) * 0.16;
      // Two bushes on opposite corners; alternate corners so a row of gardens doesn't look tiled.
      const flip = i % 2 ? 1 : -1;
      return [
        { x: g.x + g.w * (0.5 + 0.3 * flip), y: 0.06 + s * 0.35, z: g.y + g.h * 0.2, sx: s, sy: s * 0.7, sz: s },
        { x: g.x + g.w * (0.5 - 0.3 * flip), y: 0.06 + s * 0.35, z: g.y + g.h * 0.8, sx: s, sy: s * 0.7, sz: s },
      ];
    });
    const dashes: Item[] = laneDashes(segments).map((d) => ({
      x: d.x,
      y: ROAD_Y + 0.005,
      z: d.z,
      sx: d.horizontal ? d.len : 0.07,
      sy: 1,
      sz: d.horizontal ? 0.07 : d.len,
    }));
    const lamps = lampPosts(layout);
    const poles: Item[] = lamps.map((l) => ({ x: l.x, y: -0.04, z: l.z, sx: 1, sy: 1.5, sz: 1 }));
    const bulbs: Item[] = lamps.map((l) => ({ x: l.x, y: 1.5, z: l.z, sx: 1, sy: 1, sz: 1 }));
    const trunks: Item[] = forest.map((t) => ({ x: t.x, y: 0, z: t.z, sx: t.scale, sy: t.scale * 0.7, sz: t.scale }));
    const canopies: Item[] = forest.map((t, i) => ({
      x: t.x,
      y: t.scale * 1.05,
      z: t.z,
      sx: t.scale,
      sy: t.scale * 1.15,
      sz: t.scale,
      ry: i * 1.7,
      color: GREENS[Math.floor(t.hue * GREENS.length)],
    }));
    return { sidewalks, curbs, plazas, paving, lawns, bushes, dashes, poles, bulbs, trunks, canopies };
  }, [layout]);

  const ring = ringRoadSize(layout);
  const belt = beltSize(layout);
  return (
    <group>
      {/* Grass everywhere, a darker field beyond the green belt. */}
      <mesh rotation-x={-Math.PI / 2} position-y={ROAD_Y - 0.03} receiveShadow>
        <planeGeometry args={[layout.size * 8, layout.size * 8]} />
        <meshStandardMaterial color="#16201a" roughness={1} />
      </mesh>
      <mesh rotation-x={-Math.PI / 2} position-y={ROAD_Y - 0.02} receiveShadow>
        <planeGeometry args={[belt, belt]} />
        <meshStandardMaterial color="#1d2c22" roughness={1} />
      </mesh>
      {/* Asphalt: the ring road plus every street between lots. */}
      <mesh rotation-x={-Math.PI / 2} position-y={ROAD_Y} receiveShadow>
        <planeGeometry args={[ring, ring]} />
        <meshStandardMaterial color="#1a1d23" roughness={0.95} />
      </mesh>
      <Instanced items={scene.dashes} geometry={PLANE} color="#c8b46a" roughness={0.6} receiveShadow={false} />
      <Traffic layout={layout} />
      <Instanced items={scene.sidewalks} geometry={BOX} color="#3c434d" />
      <Instanced items={scene.curbs} geometry={BOX} color="#545c67" roughness={0.6} />
      <Instanced items={scene.plazas} geometry={BOX} color="#20262e" />
      <Instanced items={scene.paving} geometry={PLANE} color="#2a313b" />
      <Instanced items={scene.lawns} geometry={BOX} color="#2c5a39" />
      <Instanced items={scene.bushes} geometry={BUSH} color="#3d7a45" castShadow />
      <Instanced items={scene.trunks} geometry={TRUNK} color="#5b4632" castShadow />
      <Instanced items={scene.canopies} geometry={CANOPY} color="#2f6b45" castShadow />
      <Instanced items={scene.poles} geometry={POLE} color="#8a93a0" roughness={0.5} castShadow />
      <Instanced items={scene.bulbs} geometry={BULB} color="#ffe2a8" emissive="#ffcf7a" receiveShadow={false} />
    </group>
  );
}
