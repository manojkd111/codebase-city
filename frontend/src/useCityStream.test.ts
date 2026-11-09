import { describe, expect, it } from "vitest";
import { applyEvents, EMPTY } from "./useCityStream";
import type { Envelope } from "./types";

const base = EMPTY;

const env = (seq: number, type: Envelope["type"], data: unknown): Envelope =>
  ({ repo_id: 1, seq, ts: "", type, data }) as Envelope;

const building = (path: string) => ({
  path,
  district: "src",
  language: "python",
  size_bytes: 10,
  loc: 1,
  functions: 0,
  complexity_total: 0,
  complexity_max: 0,
  churn_30: 0,
  churn_90: 0,
  coverage: null,
  risk: 0,
  parse_error: false,
  is_test: false,
});

describe("applyEvents", () => {
  const events = [
    env(1, "ingest.started", { source: "x" }),
    env(2, "city.district_added", { name: "src", file_count: 2, total_bytes: 20 }),
    env(3, "city.building_added", building("a.py")),
    env(4, "city.building_added", building("b.py")),
    env(5, "city.road_added", { src: "src", dst: "lib", imports: 3 }),
    env(6, "ingest.completed", { files: 2 }),
  ];

  it("builds the city from a replay", () => {
    const s = applyEvents(base, events, 0);
    expect(s.status).toBe("ready");
    expect(s.districts.map((d) => d.name)).toEqual(["src"]);
    expect(s.buildings.map((b) => b.path)).toEqual(["a.py", "b.py"]);
    expect(s.roads).toHaveLength(1);
    expect(s.lastSeq).toBe(6);
  });

  it("ignores events it has already seen (reconnect overlap)", () => {
    const first = applyEvents(base, events.slice(0, 4), 0);
    const again = applyEvents(first, events.slice(2), 100);
    expect(again.buildings.map((b) => b.path)).toEqual(["a.py", "b.py"]);
    expect(again.roads).toHaveLength(1);
  });

  it("staggers spawn times so a replayed city rises in sequence", () => {
    const s = applyEvents(base, events, 1000);
    expect(s.buildings[1].spawn).toBeGreaterThan(s.buildings[0].spawn);
  });

  it("records failure", () => {
    const s = applyEvents(base, [env(1, "ingest.failed", { error: "clone failed" })], 0);
    expect(s.status).toBe("failed");
    expect(s.error).toBe("clone failed");
  });
});

describe("agents, findings and fixes", () => {
  const city = applyEvents(
    base,
    [
      env(1, "ingest.started", { source: "x" }),
      env(2, "city.district_added", { name: "src", file_count: 1, total_bytes: 10 }),
      env(3, "city.building_added", building("a.py")),
      env(4, "ingest.completed", { files: 1 }),
    ],
    0,
  );

  it("tracks drones, findings and a fix through to its pull request", () => {
    const s = applyEvents(
      city,
      [
        env(5, "analysis.started", { run_id: 1, model: "m" }),
        env(6, "agent.dispatched", { run_id: 1, agent_id: "debt-1", role: "debt", files: ["a.py"], reason: "r" }),
        env(7, "agent.moved", { run_id: 1, agent_id: "debt-1", path: "a.py", action: "read" }),
        env(8, "finding.submitted", {
          run_id: 1, agent_id: "debt-1", finding_id: 9, category: "debt", severity: "high",
          file: "a.py", line_start: 1, line_end: 2, title: "t", confidence: 0.9,
        }),
        env(9, "agent.finished", {
          run_id: 1, agent_id: "debt-1", findings: 1, rejected: 0, turns: 3,
          input_tokens: 10, output_tokens: 5, error: null,
        }),
        env(10, "analysis.completed", { run_id: 1, findings: 1, duration_s: 2, input_tokens: 10, output_tokens: 5 }),
        env(11, "finding.updated", { finding_id: 9, status: "confirmed" }),
        env(12, "fix.started", { fix_id: 3, finding_id: 9 }),
        env(13, "fix.progress", { fix_id: 3, finding_id: 9, step: "edit", detail: "a.py" }),
        env(14, "fix.completed", { fix_id: 3, finding_id: 9, files_changed: 1, tests_passed: true }),
        env(15, "fix.failed", { fix_id: 3, finding_id: 9, error: "push rejected" }),
      ],
      0,
    );
    expect(s.agents["debt-1"]).toMatchObject({ path: "a.py", status: "done", findings: 1, tokens: 15 });
    expect(s.findings[9]).toMatchObject({ file: "a.py", severity: "high", status: "confirmed" });
    expect(s.analysis).toMatchObject({ status: "done", findings: 1 });
    expect(s.fixes[3]).toMatchObject({ status: "ready", testsPassed: true, error: "push rejected" }); // PR failure keeps the fix
    const opened = applyEvents(s, [env(16, "fix.pr_opened", { fix_id: 3, finding_id: 9, url: "u" })], 0);
    expect(opened.fixes[3]).toMatchObject({ status: "pr_opened", url: "u", error: null });
    expect(opened.findingsVersion).toBeGreaterThan(s.findingsVersion);
  });

  it("a drone moving does not touch the city arrays (no re-layout)", () => {
    const s = applyEvents(
      city,
      [
        env(5, "agent.dispatched", { run_id: 1, agent_id: "x", role: "tests", files: [], reason: "" }),
        env(6, "agent.moved", { run_id: 1, agent_id: "x", path: "a.py", action: "read" }),
      ],
      0,
    );
    expect(s.buildings).toBe(city.buildings);
    expect(s.districts).toBe(city.districts);
    expect(s.agents).not.toBe(city.agents);
  });

  it("a new analysis run lands the previous drones", () => {
    const s1 = applyEvents(city, [env(5, "agent.dispatched", { run_id: 1, agent_id: "x", role: "tests", files: [], reason: "" })], 0);
    const s2 = applyEvents(s1, [env(6, "analysis.started", { run_id: 2, model: "m" })], 0);
    expect(s2.agents).toEqual({});
  });
});

describe("live agent activity", () => {
  it("tracks turns, tokens, current tool and logs every real event", () => {
    const s = applyEvents(
      base,
      [
        env(1, "analysis.started", { run_id: 1, model: "m" }),
        env(2, "agent.dispatched", { run_id: 1, agent_id: "security-1", role: "security", files: ["a.py"], reason: "" }),
        env(3, "agent.turn", {
          run_id: 1, agent_id: "security-1", turn: 1, max_turns: 30,
          input_tokens: 1000, output_tokens: 200, tool_calls: 1, duration_ms: 900,
        }),
        env(4, "agent.tool_call", {
          run_id: 1, agent_id: "security-1", tool: "read_file", target: "a.py:1-end", ok: true, duration_ms: 3, note: "",
        }),
        env(5, "agent.tool_call", {
          run_id: 1, agent_id: "security-1", tool: "submit_finding", target: "a.py:9 x", ok: false, duration_ms: 1,
          note: "Finding rejected: evidence was not found at lines 9-9",
        }),
      ],
      0,
    );
    expect(s.agents["security-1"]).toMatchObject({
      turns: 1, maxTurns: 30, tokensIn: 1000, tokensOut: 200, lastTool: "submit_finding", toolCalls: 2,
    });
    expect(s.analysis).toMatchObject({ tokensIn: 1000, tokensOut: 200 });
    const texts = s.activity.map((a) => a.text);
    expect(texts).toContain("reads a.py:1-end");
    expect(s.activity.at(-1)).toMatchObject({ tone: "warn", role: "security" });
    expect(s.activity.at(-1)!.text).toContain("evidence was not found");
  });

  it("uses the server's billed cost, and treats a total as unknown if any agent's cost is", () => {
    const turn = (seq: number, agent: string, cost?: number) =>
      env(seq, "agent.turn", {
        run_id: 1, agent_id: agent, turn: 1, max_turns: 30, input_tokens: 1000, output_tokens: 200,
        tool_calls: 0, duration_ms: 1, ...(cost === undefined ? {} : { cache_read_tokens: 800, cost_usd: cost }),
      });
    const dispatch = (seq: number, agent: string) =>
      env(seq, "agent.dispatched", { run_id: 1, agent_id: agent, role: "debt", files: [], reason: "" });
    const start = env(1, "analysis.started", { run_id: 1, model: "m" });

    const s = applyEvents(base, [start, dispatch(2, "a"), dispatch(3, "b"), turn(4, "a", 0.5), turn(5, "b", 0.25)], 0);
    expect(s.agents["a"].costUsd).toBe(0.5);
    expect(s.analysis!.costUsd).toBe(0.75);

    // An event recorded before cost tracking (no cost_usd) leaves the cost unknown, not zero.
    const old = applyEvents(base, [start, dispatch(2, "a"), dispatch(3, "b"), turn(4, "a", 0.5), turn(5, "b")], 0);
    expect(old.agents["b"].costUsd).toBeNull();
    expect(old.analysis!.costUsd).toBeNull();
  });

  it("caps the activity log", () => {
    const evs = [env(1, "analysis.started", { run_id: 1, model: "m" })];
    for (let i = 0; i < 500; i++)
      evs.push(env(i + 2, "finding.updated", { finding_id: 1, status: "open" }));
    expect(applyEvents(base, evs, 0).activity).toHaveLength(400);
  });
});
