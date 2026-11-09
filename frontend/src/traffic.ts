// Traffic: a road network from the street centerlines, and cars that drive it.
// Pure and deterministic (seeded), so it's testable and the same city always gets the same traffic.
// Purely decorative: nothing here comes from the event stream.

import type { Segment } from "./scenery";

export interface Node {
  x: number;
  z: number;
}
export interface Edge {
  a: number; // node ids
  b: number;
  len: number;
}
export interface RoadGraph {
  nodes: Node[];
  edges: Edge[];
  /** node id -> ids of the edges that touch it */
  adj: number[][];
}

const EPS = 1e-3;
const MIN_EDGE = 0.5; // shorter pieces (slivers where streets nearly meet) aren't worth driving

/** Split every street wherever another one meets or crosses it, so junctions become nodes. */
export function roadGraph(segments: Segment[]): RoadGraph {
  const nodes: Node[] = [];
  const ids = new Map<string, number>();
  const node = (x: number, z: number) => {
    const k = `${Math.round(x * 100)},${Math.round(z * 100)}`;
    let id = ids.get(k);
    if (id === undefined) {
      id = nodes.length;
      ids.set(k, id);
      nodes.push({ x, z });
    }
    return id;
  };
  const horizontal = (s: Segment) => Math.abs(s.z1 - s.z2) < EPS;
  const edges: Edge[] = [];
  for (const s of segments) {
    const h = horizontal(s);
    const lo = h ? Math.min(s.x1, s.x2) : Math.min(s.z1, s.z2);
    const hi = h ? Math.max(s.x1, s.x2) : Math.max(s.z1, s.z2);
    const at = h ? s.z1 : s.x1;
    // Cut points along this street: its ends, plus every perpendicular street that touches it.
    const cuts = [lo, hi];
    for (const o of segments) {
      if (o === s || horizontal(o) === h) continue;
      const oAt = h ? o.x1 : o.z1; // where the other street sits along this one
      const oLo = h ? Math.min(o.z1, o.z2) : Math.min(o.x1, o.x2);
      const oHi = h ? Math.max(o.z1, o.z2) : Math.max(o.x1, o.x2);
      if (oAt > lo - EPS && oAt < hi + EPS && at > oLo - EPS && at < oHi + EPS) cuts.push(oAt);
    }
    cuts.sort((p, q) => p - q);
    let prev = cuts[0];
    for (const c of cuts.slice(1)) {
      if (c - prev < MIN_EDGE) continue;
      const a = h ? node(prev, at) : node(at, prev);
      const b = h ? node(c, at) : node(at, c);
      edges.push({ a, b, len: c - prev });
      prev = c;
    }
  }
  const adj: number[][] = nodes.map(() => []);
  edges.forEach((e, i) => {
    adj[e.a].push(i);
    adj[e.b].push(i);
  });
  return { nodes, edges, adj };
}

export interface Car {
  edge: number;
  /** driving from edge.a to edge.b (true) or back */
  forward: boolean;
  /** distance travelled along the current edge */
  s: number;
  speed: number;
  color: string;
}

const COLORS = ["#d9534f", "#f0ad4e", "#5bc0de", "#e8e8e8", "#3a3f47", "#7a5cff", "#2e8b57", "#c0c6cf"];

/** Roughly one car per `spacing` units of road, capped so a big city stays cheap. */
export function spawnCars(g: RoadGraph, rand: () => number, spacing = 9, max = 60): Car[] {
  if (!g.edges.length) return [];
  const total = g.edges.reduce((n, e) => n + e.len, 0);
  const count = Math.min(max, Math.max(4, Math.round(total / spacing)));
  return Array.from({ length: count }, () => {
    const edge = Math.floor(rand() * g.edges.length);
    return {
      edge,
      forward: rand() < 0.5,
      s: rand() * g.edges[edge].len,
      speed: 2 + rand() * 1.4,
      color: COLORS[Math.floor(rand() * COLORS.length)],
    };
  });
}

/** Drive `dist` further. At a junction pick another street at random; U-turn only at a dead end. */
export function advance(g: RoadGraph, car: Car, dist: number, rand: () => number): void {
  car.s += dist;
  for (let guard = 0; guard < 8 && car.s >= g.edges[car.edge].len; guard++) {
    const e = g.edges[car.edge];
    car.s -= e.len;
    const at = car.forward ? e.b : e.a;
    const options = g.adj[at].filter((i) => i !== car.edge);
    const next = options.length ? options[Math.floor(rand() * options.length)] : car.edge;
    car.edge = next;
    car.forward = g.edges[next].a === at;
  }
}

/** Where the car is and which way it faces, in its right-hand lane `lane` units off the centerline. */
export function carPose(g: RoadGraph, car: Car, lane: number): { x: number; z: number; dx: number; dz: number } {
  const e = g.edges[car.edge];
  const from = g.nodes[car.forward ? e.a : e.b];
  const to = g.nodes[car.forward ? e.b : e.a];
  const dx = (to.x - from.x) / e.len;
  const dz = (to.z - from.z) / e.len;
  const t = Math.min(car.s, e.len);
  // Right of forward (dx, dz) with y up is (-dz, dx).
  return { x: from.x + dx * t - dz * lane, z: from.z + dz * t + dx * lane, dx, dz };
}
