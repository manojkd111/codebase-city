// Streets, sidewalks, lamps, gardens and trees, all derived from the city layout.
// Nothing here moves or resizes a building: streets live in the margin between lots, and
// gardens only use grid cells past a folder's announced file count, which never get a building.

import type { CityLayout, Rect } from "./layout";

export const SIDEWALK = 0.32;
export const ROAD_Y = -0.12; // road surface; sidewalks sit above it, plots at y = 0 under the buildings
const RING = 0.8; // half-width of the ring road outside the outermost lots (matches STREET / 2)

export interface Segment {
  x1: number;
  z1: number;
  x2: number;
  z2: number;
}

export interface Dash {
  x: number;
  z: number;
  len: number;
  horizontal: boolean;
}

export interface Tree {
  x: number;
  z: number;
  scale: number;
  hue: number; // 0..1, picks a green
}

/** Deterministic PRNG (mulberry32), so the scenery is identical on every load and replay. */
export function rng(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function hashString(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return h >>> 0;
}

/** Margin between a lot's rect and its inner plot: half a street on every side. */
function margin(lot: { rect: Rect; inner: Rect }) {
  return lot.inner.x - lot.rect.x;
}

export function sidewalkWidth(lot: { rect: Rect; inner: Rect }) {
  return Math.min(SIDEWALK, margin(lot) * 0.45);
}

/** Street centerlines: every lot edge, with shared and collinear edges merged into one interval. */
export function streetCenterlines(layout: CityLayout): Segment[] {
  const key = (v: number) => Math.round(v * 1000) / 1000;
  const horiz = new Map<number, [number, number][]>(); // z -> [x1, x2]
  const vert = new Map<number, [number, number][]>(); // x -> [z1, z2]
  const add = (m: Map<number, [number, number][]>, at: number, a: number, b: number) => {
    const k = key(at);
    if (!m.has(k)) m.set(k, []);
    m.get(k)!.push([Math.min(a, b), Math.max(a, b)]);
  };
  for (const { rect: r } of layout.lots.values()) {
    add(horiz, r.y, r.x, r.x + r.w);
    add(horiz, r.y + r.h, r.x, r.x + r.w);
    add(vert, r.x, r.y, r.y + r.h);
    add(vert, r.x + r.w, r.y, r.y + r.h);
  }
  const merge = (spans: [number, number][]) => {
    spans.sort((p, q) => p[0] - q[0]);
    const out: [number, number][] = [];
    for (const [a, b] of spans) {
      const last = out.at(-1);
      if (last && a <= last[1] + 1e-6) last[1] = Math.max(last[1], b);
      else out.push([a, b]);
    }
    return out;
  };
  const segs: Segment[] = [];
  for (const [z, spans] of horiz) for (const [a, b] of merge(spans)) segs.push({ x1: a, z1: z, x2: b, z2: z });
  for (const [x, spans] of vert) for (const [a, b] of merge(spans)) segs.push({ x1: x, z1: a, x2: x, z2: b });
  return segs;
}

/** Dashed centre line, stopping short of each end so dashes don't run through junctions. */
export function laneDashes(segments: Segment[], dash = 0.55, gap = 0.55, endPad = 1.0): Dash[] {
  const out: Dash[] = [];
  for (const s of segments) {
    const horizontal = s.z1 === s.z2;
    const len = horizontal ? s.x2 - s.x1 : s.z2 - s.z1;
    const usable = len - 2 * endPad;
    if (usable < dash) continue;
    const n = Math.floor((usable + gap) / (dash + gap));
    const start = (horizontal ? s.x1 : s.z1) + endPad + (usable - (n * (dash + gap) - gap)) / 2 + dash / 2;
    for (let i = 0; i < n; i++) {
      const t = start + i * (dash + gap);
      out.push(horizontal ? { x: t, z: s.z1, len: dash, horizontal } : { x: s.x1, z: t, len: dash, horizontal });
    }
  }
  return out;
}

/** Grid cells that never get a building: past each folder's announced file count. */
export function gardenCells(layout: CityLayout): Rect[] {
  const out: Rect[] = [];
  for (const lot of layout.lots.values()) {
    for (const b of lot.blocks.values()) {
      const cw = b.rect.w / b.cols;
      const ch = b.rect.h / b.rows;
      for (let s = b.count; s < b.cols * b.rows; s++) {
        const c = s % b.cols;
        const r = Math.floor(s / b.cols);
        out.push({ x: b.rect.x + c * cw, y: b.rect.y + r * ch, w: cw, h: ch });
      }
    }
  }
  return out;
}

/** Lamp posts on the four sidewalk corners of every lot. */
export function lampPosts(layout: CityLayout): { x: number; z: number }[] {
  const out: { x: number; z: number }[] = [];
  for (const lot of layout.lots.values()) {
    const sw = sidewalkWidth(lot);
    if (sw < 0.15) continue;
    const { x, y, w, h } = lot.inner;
    const o = sw / 2;
    out.push({ x: x - o, z: y - o }, { x: x + w + o, z: y - o }, { x: x - o, z: y + h + o }, { x: x + w + o, z: y + h + o });
  }
  return out;
}

export function trees(layout: CityLayout, gardens: Rect[]): Tree[] {
  const out: Tree[] = [];
  const rand = rng(hashString([...layout.lots.keys()].join("|")) ^ layout.placed.length);

  // Gardens: a few trees per empty cell, kept inside the cell.
  for (const g of gardens) {
    const side = Math.min(g.w, g.h);
    const n = side > 3 ? 3 : side > 1.6 ? 2 : 1;
    for (let i = 0; i < n; i++) {
      out.push({
        x: g.x + g.w * (0.25 + rand() * 0.5),
        z: g.y + g.h * (0.25 + rand() * 0.5),
        scale: Math.min(1, side / 2.2) * (0.75 + rand() * 0.4),
        hue: rand(),
      });
    }
  }

  // Street trees along each lot's sidewalk, spaced out and clear of the corner lamps.
  const spacing = 3.2;
  for (const lot of layout.lots.values()) {
    const sw = sidewalkWidth(lot);
    if (sw < 0.2) continue;
    const { x, y, w, h } = lot.inner;
    const o = sw / 2;
    const edges: [number, number, number, number][] = [
      [x, y - o, x + w, y - o],
      [x, y + h + o, x + w, y + h + o],
      [x - o, y, x - o, y + h],
      [x + w + o, y, x + w + o, y + h],
    ];
    for (const [ax, az, bx, bz] of edges) {
      const len = Math.hypot(bx - ax, bz - az);
      const n = Math.floor((len - 1.2) / spacing);
      for (let i = 1; i <= n; i++) {
        const t = (0.6 + ((len - 1.2) * i) / (n + 1)) / len;
        out.push({ x: ax + (bx - ax) * t, z: az + (bz - az) * t, scale: 0.45 + rand() * 0.15, hue: rand() });
      }
    }
  }

  // A green belt outside the ring road.
  const inner = layout.size / 2 + RING + 1.2;
  const outer = inner + Math.max(6, layout.size * 0.22);
  const count = Math.min(700, Math.round((outer * outer - inner * inner) * 0.07));
  for (let i = 0; i < count; i++) {
    const px = (rand() * 2 - 1) * outer;
    const pz = (rand() * 2 - 1) * outer;
    if (Math.max(Math.abs(px), Math.abs(pz)) < inner) continue;
    out.push({ x: px, z: pz, scale: 0.7 + rand() * 0.8, hue: rand() });
  }
  return out;
}

export function ringRoadSize(layout: CityLayout) {
  return layout.size + 2 * RING;
}

export function beltSize(layout: CityLayout) {
  return 2 * (layout.size / 2 + RING + 1.2 + Math.max(6, layout.size * 0.22));
}
