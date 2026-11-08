import { describe, expect, it } from "vitest";
import { dirname, layoutCity, squarify } from "./layout";
import type { Building, District } from "./types";

const bld = (path: string, district: string, size = 1000, loc = 50): Building => ({
  path,
  district,
  language: "python",
  size_bytes: size,
  loc,
  functions: 1,
  complexity_total: 1,
  complexity_max: 1,
  churn_30: 0,
  churn_90: 0,
  coverage: null,
  risk: 1,
  parse_error: false,
  is_test: false,
});

describe("squarify", () => {
  it("tiles the rectangle exactly, with areas proportional to value", () => {
    const rect = { x: 0, y: 0, w: 10, h: 6 };
    const out = squarify([6, 6, 4, 3, 2, 2, 1], (v) => v, rect);
    const area = out.reduce((s, o) => s + o.rect.w * o.rect.h, 0);
    expect(area).toBeCloseTo(60, 6);
    for (const o of out) expect((o.rect.w * o.rect.h) / 60).toBeCloseTo(o.item / 24, 6);
    for (const o of out) {
      expect(o.rect.x).toBeGreaterThanOrEqual(-1e-9);
      expect(o.rect.y).toBeGreaterThanOrEqual(-1e-9);
      expect(o.rect.x + o.rect.w).toBeLessThanOrEqual(10 + 1e-9);
      expect(o.rect.y + o.rect.h).toBeLessThanOrEqual(6 + 1e-9);
    }
  });

  it("returns nothing for empty or zero input", () => {
    expect(squarify([], (v: number) => v, { x: 0, y: 0, w: 1, h: 1 })).toEqual([]);
    expect(squarify([0, 0], (v) => v, { x: 0, y: 0, w: 1, h: 1 })).toEqual([]);
  });
});

describe("layoutCity", () => {
  const districts: District[] = [
    { name: "src", file_count: 3, total_bytes: 3000, folders: { "": 3 } },
    { name: "tests", file_count: 2, total_bytes: 2000, folders: { "": 2 } },
  ];

  it("keeps every building inside its own lot", () => {
    const bs = [bld("a", "src"), bld("b", "src"), bld("c", "src"), bld("t1", "tests"), bld("t2", "tests")];
    const { lots, placed } = layoutCity(districts, bs);
    expect(placed).toHaveLength(5);
    for (const p of placed) {
      const r = lots.get(p.building.district)!.inner;
      expect(p.x - p.footprint / 2).toBeGreaterThanOrEqual(r.x - 1e-9);
      expect(p.x + p.footprint / 2).toBeLessThanOrEqual(r.x + r.w + 1e-9);
      expect(p.z - p.footprint / 2).toBeGreaterThanOrEqual(r.y - 1e-9);
      expect(p.z + p.footprint / 2).toBeLessThanOrEqual(r.y + r.h + 1e-9);
    }
  });

  it("never moves a building when later ones arrive", () => {
    const first = layoutCity(districts, [bld("a", "src")]).placed[0];
    const later = layoutCity(districts, [bld("a", "src"), bld("t1", "tests"), bld("b", "src")]).placed[0];
    expect(later).toEqual(first);
  });

  it("groups each folder into its own block, and keeps buildings inside it", () => {
    const ds: District[] = [
      { name: "src", file_count: 5, total_bytes: 5000, folders: { src: 2, "src/cli": 2, "src/web": 1 } },
    ];
    const bs = [bld("src/a.py", "src"), bld("src/b.py", "src"), bld("src/cli/c.py", "src"), bld("src/cli/d.py", "src"), bld("src/web/e.py", "src")];
    const { lots, placed } = layoutCity(ds, bs);
    const blocks = lots.get("src")!.blocks;
    expect([...blocks.keys()].sort()).toEqual(["src", "src/cli", "src/web"]);
    expect(blocks.get("src/cli")!.label).toBe("cli");
    for (const p of placed) {
      const r = blocks.get(dirname(p.building.path))!.rect;
      expect(p.x - p.footprint / 2).toBeGreaterThanOrEqual(r.x - 1e-9);
      expect(p.x + p.footprint / 2).toBeLessThanOrEqual(r.x + r.w + 1e-9);
      expect(p.z - p.footprint / 2).toBeGreaterThanOrEqual(r.y - 1e-9);
      expect(p.z + p.footprint / 2).toBeLessThanOrEqual(r.y + r.h + 1e-9);
    }
    // blocks don't overlap
    const rs = [...blocks.values()].map((b) => b.rect);
    for (let i = 0; i < rs.length; i++)
      for (let j = i + 1; j < rs.length; j++) {
        const a = rs[i], b = rs[j];
        const overlap = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x) > 1e-9 && Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y) > 1e-9;
        expect(overlap).toBe(false);
      }
  });

  it("never moves a building when later ones arrive in other folders", () => {
    const ds: District[] = [{ name: "src", file_count: 3, total_bytes: 3000, folders: { src: 1, "src/cli": 2 } }];
    const first = layoutCity(ds, [bld("src/a.py", "src")]).placed[0];
    const later = layoutCity(ds, [bld("src/a.py", "src"), bld("src/cli/c.py", "src"), bld("src/cli/d.py", "src")]).placed[0];
    expect(later).toEqual(first);
  });

  it("skips buildings whose district is unknown", () => {
    expect(layoutCity(districts, [bld("x", "nope")]).placed).toEqual([]);
  });
});
