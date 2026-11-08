// Deterministic city layout. Districts are known before any building arrives
// (see docs/architecture.md), so every lot and plot is fixed up front: buildings
// rise into their slot and nothing already standing ever moves.

import type { Building, District } from "./types";

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** One folder's block inside a district: a fixed grid of plots. */
export interface Block {
  folder: string; // posix dirname of its files, "" for the repo root
  label: string; // folder relative to the district, for display
  rect: Rect;
  cols: number;
  rows: number;
  count: number; // files announced for this folder; grid cells past it never get a building
}

export interface Lot {
  district: District;
  rect: Rect; // world x/z, centred on the origin
  inner: Rect; // rect minus the street margin
  blocks: Map<string, Block>;
}

export interface Placed {
  building: Building;
  x: number;
  z: number;
  footprint: number;
  height: number;
}

export interface CityLayout {
  size: number;
  lots: Map<string, Lot>;
  placed: Placed[];
}

/** Squarified treemap (Bruls et al.): keeps cells close to square. */
export function squarify<T>(items: T[], value: (t: T) => number, rect: Rect): { item: T; rect: Rect }[] {
  const total = items.reduce((s, t) => s + Math.max(value(t), 0), 0);
  if (!items.length || total <= 0 || rect.w <= 0 || rect.h <= 0) return [];
  const scale = (rect.w * rect.h) / total;
  const nodes = items
    .map((item) => ({ item, area: Math.max(value(item), 0) * scale }))
    .filter((n) => n.area > 0)
    .sort((a, b) => b.area - a.area);

  const out: { item: T; rect: Rect }[] = [];
  let free = { ...rect };
  let row: typeof nodes = [];

  const worst = (r: typeof nodes, side: number) => {
    const s = r.reduce((a, n) => a + n.area, 0);
    const max = Math.max(...r.map((n) => n.area));
    const min = Math.min(...r.map((n) => n.area));
    return Math.max((side * side * max) / (s * s), (s * s) / (side * side * min));
  };

  const flush = () => {
    const s = row.reduce((a, n) => a + n.area, 0);
    if (free.w >= free.h) {
      const colW = s / free.h;
      let y = free.y;
      for (const n of row) {
        const h = n.area / colW;
        out.push({ item: n.item, rect: { x: free.x, y, w: colW, h } });
        y += h;
      }
      free = { x: free.x + colW, y: free.y, w: free.w - colW, h: free.h };
    } else {
      const rowH = s / free.w;
      let x = free.x;
      for (const n of row) {
        const w = n.area / rowH;
        out.push({ item: n.item, rect: { x, y: free.y, w, h: rowH } });
        x += w;
      }
      free = { x: free.x, y: free.y + rowH, w: free.w, h: free.h - rowH };
    }
    row = [];
  };

  let i = 0;
  while (i < nodes.length) {
    const side = Math.min(free.w, free.h);
    if (!row.length || worst([...row, nodes[i]], side) <= worst(row, side)) {
      row.push(nodes[i++]);
    } else {
      flush();
    }
  }
  if (row.length) flush();
  return out;
}

export const STREET = 1.6;
const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

export function buildingHeight(complexity: number): number {
  return 0.5 + Math.sqrt(Math.max(complexity, 1)) * 0.9;
}

export function layoutCity(districts: District[], buildings: Building[]): CityLayout {
  const files = districts.reduce((s, d) => s + d.file_count, 0);
  const bytes = districts.reduce((s, d) => s + d.total_bytes, 0);
  const avg = files ? bytes / files : 1;
  const size = Math.sqrt(Math.max(files, 1)) * 6 + 10;

  // Lot area blends file count and bytes, so a district of many tiny files
  // still has room for its buildings and one huge file doesn't swallow the map.
  const cells = squarify(
    districts,
    (d) => 0.5 * d.file_count + (0.5 * d.total_bytes) / avg + 0.25,
    { x: -size / 2, y: -size / 2, w: size, h: size },
  );

  const lots = new Map<string, Lot>();
  for (const { item: d, rect } of cells) {
    const m = Math.min(STREET / 2, rect.w / 4, rect.h / 4);
    const inner = { x: rect.x + m, y: rect.y + m, w: rect.w - 2 * m, h: rect.h - 2 * m };
    lots.set(d.name, { district: d, rect, inner, blocks: layoutBlocks(d, inner, buildings) });
  }

  // Slots are handed out in arrival order within each folder, which the backend keeps stable.
  const used = new Map<string, number>();
  const placed: Placed[] = [];
  for (const b of buildings) {
    const lot = lots.get(b.district);
    const block = lot?.blocks.get(dirname(b.path));
    if (!lot || !block) continue;
    const key = `${b.district}\0${block.folder}`;
    const slot = used.get(key) ?? 0;
    used.set(key, slot + 1);
    const { rect: inner, cols, rows } = block;
    const cw = inner.w / cols;
    const ch = inner.h / rows;
    const s = slot % (cols * rows); // more files than announced shouldn't happen; wrap rather than move others
    const c = s % cols;
    const r = Math.floor(s / cols);
    const davg = lot.district.total_bytes / Math.max(lot.district.file_count, 1) || 1;
    const frac = clamp(0.3 + 0.25 * Math.sqrt(b.size_bytes / davg), 0.3, 0.7);
    placed.push({
      building: b,
      x: inner.x + (c + 0.5) * cw,
      z: inner.y + (r + 0.5) * ch,
      footprint: Math.min(cw, ch) * frac,
      height: buildingHeight(b.complexity_total),
    });
  }
  return { size, lots, placed };
}

export const ALLEY = 0.7;

export function dirname(path: string): string {
  const i = path.lastIndexOf("/");
  return i < 0 ? "" : path.slice(0, i);
}

function gridFor(n: number, r: Rect): { cols: number; rows: number } {
  n = Math.max(n, 1);
  const cols = Math.min(n, Math.max(1, Math.round(Math.sqrt((n * r.w) / Math.max(r.h, 1e-6)))));
  return { cols, rows: Math.ceil(n / cols) };
}

/** Splits a district's lot into one block per folder, sized by file count. */
function layoutBlocks(d: District, inner: Rect, buildings: Building[]): Map<string, Block> {
  // Folder counts come with the district event. Events stored before they existed have
  // none, so fall back to the buildings seen so far (plots may then shift while loading).
  let counts = Object.entries(d.folders ?? {});
  if (!counts.length) {
    const seen = new Map<string, number>();
    for (const b of buildings) {
      if (b.district === d.name) seen.set(dirname(b.path), (seen.get(dirname(b.path)) ?? 0) + 1);
    }
    counts = [...seen.entries()];
  }
  if (!counts.length) counts = [[d.name === "(root)" ? "" : d.name, Math.max(d.file_count, 1)]];
  counts.sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)); // stable input -> stable treemap

  const label = (folder: string) => {
    if (folder === d.name) return d.name;
    if (folder.startsWith(d.name + "/")) return folder.slice(d.name.length + 1);
    return folder || "/";
  };

  const blocks = new Map<string, Block>();
  if (counts.length === 1) {
    const [folder, n] = counts[0];
    blocks.set(folder, { folder, label: label(folder), rect: inner, count: n, ...gridFor(n, inner) });
    return blocks;
  }
  for (const { item: [folder, n], rect } of squarify(counts, ([, n]) => n, inner)) {
    const a = Math.min(ALLEY / 2, rect.w / 6, rect.h / 6);
    const r = { x: rect.x + a, y: rect.y + a, w: rect.w - 2 * a, h: rect.h - 2 * a };
    blocks.set(folder, { folder, label: label(folder), rect: r, count: n, ...gridFor(n, r) });
  }
  return blocks;
}

// Sequential risk scale: calm blue → amber → hot red. Tests are pulled toward grey
// so the eye lands on production code first.
const STOPS: [number, [number, number, number]][] = [
  [0, [0.3, 0.45, 0.64]],
  [0.55, [0.88, 0.71, 0.29]],
  [1, [0.88, 0.32, 0.25]],
];

export function riskColor(risk: number, maxRisk: number, isTest: boolean): [number, number, number] {
  const t = maxRisk > 0 ? clamp(risk / maxRisk, 0, 1) : 0;
  let rgb: [number, number, number] = STOPS[STOPS.length - 1][1];
  for (let i = 1; i < STOPS.length; i++) {
    const [t1, c1] = STOPS[i];
    if (t <= t1) {
      const [t0, c0] = STOPS[i - 1];
      const k = (t - t0) / (t1 - t0);
      rgb = [c0[0] + (c1[0] - c0[0]) * k, c0[1] + (c1[1] - c0[1]) * k, c0[2] + (c1[2] - c0[2]) * k];
      break;
    }
  }
  if (isTest) {
    const g = 0.36;
    rgb = [rgb[0] * 0.12 + g * 0.88, rgb[1] * 0.12 + g * 0.88, rgb[2] * 0.12 + g * 0.88];
  }
  return rgb;
}
