import { describe, expect, it } from "vitest";
import { rng } from "./scenery";
import { advance, carPose, roadGraph, spawnCars, type Car } from "./traffic";

const seg = (x1: number, z1: number, x2: number, z2: number) => ({ x1, z1, x2, z2 });
const plus = [seg(-5, 0, 5, 0), seg(0, -5, 0, 5)];

describe("road network", () => {
  it("splits crossing streets into a junction", () => {
    const g = roadGraph(plus);
    expect(g.nodes).toHaveLength(5);
    expect(g.edges).toHaveLength(4);
    const centre = g.nodes.findIndex((n) => n.x === 0 && n.z === 0);
    expect(g.adj[centre]).toHaveLength(4);
    expect(g.edges.every((e) => e.len === 5)).toBe(true);
  });

  it("treats a street ending on another as a T junction", () => {
    const g = roadGraph([seg(-5, 0, 5, 0), seg(0, 0, 0, 5)]);
    expect(g.nodes).toHaveLength(4);
    expect(g.edges).toHaveLength(3);
  });

  it("joins a ring road at its corners", () => {
    const g = roadGraph([seg(0, 0, 10, 0), seg(0, 10, 10, 10), seg(0, 0, 0, 10), seg(10, 0, 10, 10)]);
    expect(g.nodes).toHaveLength(4);
    expect(g.adj.every((a) => a.length === 2)).toBe(true);
  });
});

describe("driving", () => {
  it("never U-turns at a junction, but does at a dead end", () => {
    const g = roadGraph(plus);
    const rand = rng(1);
    const centre = g.nodes.findIndex((n) => n.x === 0 && n.z === 0);
    const inbound = g.adj[centre][0];
    for (let i = 0; i < 50; i++) {
      const car: Car = { edge: inbound, forward: g.edges[inbound].b === centre, s: 4.9, speed: 1, color: "" };
      advance(g, car, 0.2, rand);
      expect(car.edge).not.toBe(inbound);
      expect(g.edges[car.edge].a === centre ? car.forward : !car.forward).toBe(true); // leaving the junction
    }
    const lone = roadGraph([seg(0, 0, 10, 0)]);
    const car: Car = { edge: 0, forward: true, s: 9.5, speed: 1, color: "" };
    advance(lone, car, 1, rand);
    expect(car).toMatchObject({ edge: 0, forward: false });
    expect(car.s).toBeCloseTo(0.5);
  });

  it("drives on the right-hand side of the street", () => {
    const g = roadGraph([seg(0, 0, 10, 0)]);
    const east = carPose(g, { edge: 0, forward: true, s: 5, speed: 1, color: "" }, 0.4);
    const west = carPose(g, { edge: 0, forward: false, s: 5, speed: 1, color: "" }, 0.4);
    // Facing +x with y up, right is +z; facing -x it's -z. Opposite directions use opposite lanes.
    expect(east).toMatchObject({ x: 5, dx: 1 });
    expect(east.z).toBeCloseTo(0.4);
    expect(west.z).toBeCloseTo(-0.4);
  });

  it("spawns the same traffic for the same seed, scaled to the road length", () => {
    const g = roadGraph(plus);
    expect(spawnCars(g, rng(7))).toEqual(spawnCars(g, rng(7)));
    expect(spawnCars(g, rng(7), 5).length).toBe(4); // 20 units of road / 5
    expect(spawnCars(roadGraph([]), rng(7))).toEqual([]);
  });
});
