import { describe, expect, it } from "vitest";
import { LEAVE_TIMEOUT_S, nextPhase, type Phase } from "./Carrier";

const quiet = { running: false, allAboard: true, idleFor: 0, reachedOrbit: false, outOfSight: false };

describe("carrier flight plan", () => {
  it("flies in when a run starts and holds once it reaches its orbit", () => {
    expect(nextPhase("away", { ...quiet, running: true })).toBe("arriving");
    expect(nextPhase("arriving", { ...quiet, running: true })).toBe("arriving");
    expect(nextPhase("arriving", { ...quiet, running: true, reachedOrbit: true })).toBe("holding");
    expect(nextPhase("holding", { ...quiet, running: true, allAboard: true })).toBe("holding"); // drones not out yet
  });

  it("waits for every drone after the run, then leaves and disappears", () => {
    const done = { ...quiet, idleFor: 1 };
    expect(nextPhase("holding", { ...done, allAboard: false })).toBe("holding");
    expect(nextPhase("holding", { ...done, allAboard: true })).toBe("leaving");
    expect(nextPhase("leaving", done)).toBe("leaving");
    expect(nextPhase("leaving", { ...done, outOfSight: true })).toBe("away");
  });

  it("doesn't wait forever for a straggler", () => {
    expect(nextPhase("holding", { ...quiet, allAboard: false, idleFor: LEAVE_TIMEOUT_S + 0.1 })).toBe("leaving");
  });

  it("turns back if a new run starts while it's leaving", () => {
    expect(nextPhase("leaving", { ...quiet, running: true })).toBe("arriving");
  });

  it("never appears for a run that was already over when the page loaded", () => {
    let p: Phase = "away";
    for (let i = 0; i < 100; i++) p = nextPhase(p, { ...quiet, idleFor: i });
    expect(p).toBe("away");
  });
});
