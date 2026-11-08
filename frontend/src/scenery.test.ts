import { describe, expect, it } from "vitest";
import { layoutCity } from "./layout";
import { gardenCells, laneDashes, lampPosts, sidewalkWidth, streetCenterlines, trees } from "./scenery";
import type { Building, District } from "./types";

const bld = (path: string, district: string): Building => ({
  path,
  district,
  language: "python",
  size_bytes: 1000,
  loc: 50,
  functions: 1,
  complexity_total: 5,
  complexity_max: 2,
  churn_30: 0,
  churn_90: 0,
  coverage: null,
  risk: 1,
  parse_error: false,
  is_test: false,
});

// 3 files in src (grid 2x2 -> one empty cell), 5 in lib across two folders, 2 in tests.
const districts: District[] = [
  { name: "src", file_count: 3, total_bytes: 3000, folders: { src: 3 } },
  { name: "lib", file_count: 5, total_bytes: 5000, folders: { lib: 2, "lib/x": 3 } },
  { name: "tests", file_count: 2, total_bytes: 2000, folders: { tests: 2 } },
];
const buildings = [
  ...["a", "b", "c"].map((n) => bld(`src/${n}.py`, "src")),
  bld("lib/a.py", "lib"),
  bld("lib/b.py", "lib"),
  ...["c", "d", "e"].map((n) => bld(`lib/x/${n}.py`, "lib")),
  bld("tests/t1.py", "tests"),
  bld("tests/t2.py", "tests"),
];
const layout = layoutCity(districts, buildings);

const inside = (x: number, z: number, r: { x: number; y: number; w: number; h: number }) =>
  x > r.x + 1e-9 && x < r.x + r.w - 1e-9 && z > r.y + 1e-9 && z < r.y + r.h - 1e-9;

describe("scenery", () => {
  it("never plants a garden where a building stands, and fills every spare cell", () => {
    const gardens = gardenCells(layout);
    for (const p of layout.placed) for (const g of gardens) expect(inside(p.x, p.z, g)).toBe(false);
    const spare = [...layout.lots.values()]
      .flatMap((l) => [...l.blocks.values()])
      .reduce((n, b) => n + b.cols * b.rows - b.count, 0);
    expect(gardens).toHaveLength(spare);
    expect(spare).toBeGreaterThan(0);
  });

  it("keeps sidewalks in the street margin, outside every building plot", () => {
    for (const lot of layout.lots.values()) {
      const sw = sidewalkWidth(lot);
      expect(sw).toBeGreaterThan(0);
      expect(lot.inner.x - sw).toBeGreaterThanOrEqual(lot.rect.x - 1e-9); // still inside the lot's own margin
    }
  });

  it("merges shared lot edges into single street centerlines", () => {
    const segs = streetCenterlines(layout);
    const keys = segs.map((s) => `${s.x1.toFixed(3)},${s.z1.toFixed(3)},${s.x2.toFixed(3)},${s.z2.toFixed(3)}`);
    expect(new Set(keys).size).toBe(keys.length);
    // No two collinear segments on the same line overlap.
    for (let i = 0; i < segs.length; i++)
      for (let j = i + 1; j < segs.length; j++) {
        const [a, b] = [segs[i], segs[j]];
        if (a.z1 === a.z2 && b.z1 === b.z2 && Math.abs(a.z1 - b.z1) < 1e-3)
          expect(Math.min(a.x2, b.x2) - Math.max(a.x1, b.x1)).toBeLessThanOrEqual(1e-6);
      }
  });

  it("lane dashes stay on their street and clear of junctions", () => {
    const seg = { x1: 0, z1: 5, x2: 10, z2: 5 };
    const d = laneDashes([seg], 0.5, 0.5, 1);
    expect(d.length).toBeGreaterThan(5);
    for (const x of d) {
      expect(x.z).toBe(5);
      expect(x.x - x.len / 2).toBeGreaterThanOrEqual(1 - 1e-9);
      expect(x.x + x.len / 2).toBeLessThanOrEqual(9 + 1e-9);
    }
    expect(laneDashes([{ x1: 0, z1: 0, x2: 1.5, z2: 0 }])).toEqual([]); // too short to mark
  });

  it("is deterministic: the same city gets the same trees on every load", () => {
    const g = gardenCells(layout);
    expect(trees(layout, g)).toEqual(trees(layoutCity(districts, buildings), gardenCells(layout)));
    expect(lampPosts(layout)).toHaveLength(layout.lots.size * 4);
  });

  it("garden trees stay inside their garden cell", () => {
    const g = gardenCells(layout);
    const t = trees(layout, g);
    const perGarden = t.slice(0, g.length); // gardens are planted first, at least one tree each
    perGarden.forEach((tree) => expect(g.some((cell) => inside(tree.x, tree.z, cell))).toBe(true));
  });
});
