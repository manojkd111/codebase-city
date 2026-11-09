// One code path for live and finished cities: the WebSocket replays every stored
// event, then streams live ones. We de-duplicate by seq and reconnect from the last
// seq we saw, so a dropped socket never loses or doubles a building, a drone move or a finding.

import { useEffect, useRef, useState } from "react";
import type {
  Building,
  District,
  Envelope,
  FindingStatus,
  IngestCompleted,
  IngestProgress,
  Road,
  Role,
  Severity,
} from "./types";

export type Spawned = Building & { spawn: number };

export interface AgentState {
  id: string;
  role: Role;
  files: string[];
  reason: string;
  path: string | null;
  action: string | null;
  status: "working" | "done" | "failed";
  findings: number;
  rejected: number;
  turns: number;
  maxTurns: number;
  tokens: number;
  tokensIn: number;
  tokensOut: number;
  /** billed cost at list price, from the server; null for events recorded before it was tracked */
  costUsd: number | null;
  /** what the agent is doing right now, from the last tool call */
  lastTool: string | null;
  lastTarget: string | null;
  toolCalls: number;
  startedAt: number;
  finishedAt: number | null;
  error: string | null;
}

export interface ActivityEntry {
  seq: number;
  ts: number;
  agentId: string | null;
  role: Role | "coordinator" | "fixer" | null;
  text: string;
  tone: "info" | "ok" | "warn" | "error" | "finding";
}

const ACTIVITY_LIMIT = 400;

/** A total is only known if every part is: one agent without a recorded cost makes it unknown. */
export function sumCosts(costs: (number | null)[]): number | null {
  return costs.some((c) => c == null) ? null : costs.reduce<number>((n, c) => n + (c as number), 0);
}

export interface FindingMark {
  id: number;
  file: string;
  severity: Severity;
  category: Role;
  title: string;
  status: FindingStatus;
}

export interface FixState {
  id: number;
  findingId: number;
  status: "running" | "ready" | "failed" | "pr_opened";
  step: string | null;
  detail: string | null;
  filesChanged: number;
  testsPassed: boolean | null;
  url: string | null;
  error: string | null;
  startedAt: number;
  steps: { step: string; detail: string; ts: number }[];
}

export interface AnalysisState {
  runId: number;
  status: "running" | "done" | "failed";
  model: string;
  findings: number;
  tokensIn: number;
  tokensOut: number;
  costUsd: number | null;
  durationS: number | null;
  error: string | null;
  startedAt: number;
}

export interface CityState {
  status: "connecting" | "building" | "ready" | "failed";
  progress: IngestProgress | null;
  districts: District[];
  buildings: Spawned[];
  roads: Road[];
  completed: IngestCompleted | null;
  error: string | null;
  analysis: AnalysisState | null;
  agents: Record<string, AgentState>;
  findings: Record<number, FindingMark>;
  fixes: Record<number, FixState>;
  /** Newest last, capped: the Mission Control timeline. Every entry is a real backend event. */
  activity: ActivityEntry[];
  /** Bumps whenever a finding or fix changes, so views holding full details can refetch. */
  findingsVersion: number;
  lastSeq: number;
}

export const EMPTY: CityState = {
  status: "connecting",
  progress: null,
  districts: [],
  buildings: [],
  roads: [],
  completed: null,
  error: null,
  analysis: null,
  agents: {},
  findings: {},
  fixes: {},
  activity: [],
  findingsVersion: 0,
  lastSeq: 0,
};

const RISE_WINDOW_MS = 1800; // a replayed city of any size rises within this window

type Collections = "districts" | "buildings" | "roads" | "agents" | "findings" | "fixes" | "activity";

const TOOL_VERB: Record<string, string> = {
  read_file: "reads",
  search_code: "searches",
  get_file_metrics: "checks metrics of",
  get_imports: "traces imports of",
  submit_finding: "submits",
};

export function applyEvents(prev: CityState, events: Envelope[], now: number): CityState {
  const s: CityState = { ...prev };
  // Copy a collection only the first time this batch touches it. Untouched arrays keep their
  // identity, so a drone moving never re-lays-out the city or rebuilds the buildings mesh.
  const touched = new Set<Collections>();
  const own = <K extends Collections>(key: K): CityState[K] => {
    if (!touched.has(key)) {
      touched.add(key);
      const v = s[key];
      s[key] = (Array.isArray(v) ? [...v] : { ...v }) as CityState[K];
    }
    return s[key];
  };

  const newBuildings = events.filter((e) => e.type === "city.building_added").length;
  const stagger = newBuildings ? Math.min(25, RISE_WINDOW_MS / newBuildings) : 0;
  let nextSpawn = Math.max(now, prev.buildings.at(-1)?.spawn ?? 0);

  const agent = (id: string): AgentState | undefined => {
    const agents = own("agents");
    if (!agents[id]) return undefined;
    agents[id] = { ...agents[id] };
    return agents[id];
  };
  let ts = now;
  const fix = (id: number, findingId: number): FixState => {
    const fixes = own("fixes");
    s.findingsVersion += 1;
    fixes[id] = fixes[id]
      ? { ...fixes[id] }
      : {
          id,
          findingId,
          status: "running",
          step: null,
          detail: null,
          filesChanged: 0,
          testsPassed: null,
          url: null,
          error: null,
          startedAt: ts,
          steps: [],
        };
    return fixes[id];
  };
  let seq = 0;
  const log = (agentId: string | null, role: ActivityEntry["role"], text: string, tone: ActivityEntry["tone"]) => {
    const a = own("activity");
    a.push({ seq, ts, agentId, role, text, tone });
    if (a.length > ACTIVITY_LIMIT) a.splice(0, a.length - ACTIVITY_LIMIT);
  };
  const roleOf = (agentId: string) => s.agents[agentId]?.role ?? null;

  for (const e of events) {
    if (e.seq <= s.lastSeq) continue;
    s.lastSeq = e.seq;
    seq = e.seq;
    const parsed = Date.parse(e.ts);
    ts = Number.isNaN(parsed) ? now : parsed;
    switch (e.type) {
      case "ingest.started":
        Object.assign(s, { ...EMPTY, status: "building", lastSeq: e.seq });
        touched.clear();
        break;
      case "ingest.progress":
        s.progress = e.data;
        if (s.status === "connecting") s.status = "building";
        break;
      case "city.district_added":
        own("districts").push(e.data);
        break;
      case "city.building_added":
        own("buildings").push({ ...e.data, spawn: nextSpawn });
        nextSpawn += stagger;
        break;
      case "city.road_added":
        own("roads").push(e.data);
        break;
      case "ingest.completed":
        s.completed = e.data;
        s.status = "ready";
        break;
      case "ingest.failed":
        s.error = e.data.error;
        s.status = "failed";
        break;
      case "analysis.started":
        s.analysis = {
          runId: e.data.run_id,
          status: "running",
          model: e.data.model,
          findings: 0,
          tokensIn: 0,
          tokensOut: 0,
          costUsd: null,
          durationS: null,
          error: null,
          startedAt: ts,
        };
        s.agents = {}; // drones from an earlier run have landed
        touched.add("agents");
        log(null, "coordinator", `Run #${e.data.run_id} started on ${e.data.model}; coordinator is planning`, "info");
        break;
      case "agent.dispatched":
        own("agents")[e.data.agent_id] = {
          id: e.data.agent_id,
          role: e.data.role,
          files: e.data.files,
          reason: e.data.reason,
          path: null,
          action: null,
          status: "working",
          findings: 0,
          rejected: 0,
          turns: 0,
          maxTurns: 0,
          tokens: 0,
          tokensIn: 0,
          tokensOut: 0,
          costUsd: null,
          lastTool: null,
          lastTarget: null,
          toolCalls: 0,
          startedAt: ts,
          finishedAt: null,
          error: null,
        };
        log(e.data.agent_id, "coordinator", `Dispatched ${e.data.role} → ${e.data.files.length} files`, "info");
        break;
      case "agent.moved": {
        const a = agent(e.data.agent_id);
        if (a) Object.assign(a, { path: e.data.path, action: e.data.action });
        break;
      }
      case "agent.turn": {
        const a = agent(e.data.agent_id);
        if (a)
          Object.assign(a, {
            turns: e.data.turn,
            maxTurns: e.data.max_turns,
            tokensIn: e.data.input_tokens,
            tokensOut: e.data.output_tokens,
            tokens: e.data.input_tokens + e.data.output_tokens,
            costUsd: e.data.cost_usd ?? null,
          });
        if (s.analysis) {
          // Live run totals: sum the latest cumulative counts of every agent.
          const all = Object.values(s.agents);
          s.analysis = {
            ...s.analysis,
            tokensIn: all.reduce((n, x) => n + x.tokensIn, 0),
            tokensOut: all.reduce((n, x) => n + x.tokensOut, 0),
            costUsd: sumCosts(all.map((x) => x.costUsd)),
          };
        }
        break;
      }
      case "agent.tool_call": {
        const a = agent(e.data.agent_id);
        if (a) Object.assign(a, { lastTool: e.data.tool, lastTarget: e.data.target, toolCalls: a.toolCalls + 1 });
        const verb = TOOL_VERB[e.data.tool] ?? e.data.tool;
        const tail = e.data.ok ? "" : ` (rejected: ${e.data.note.replace(/^Finding rejected: /, "").slice(0, 90)})`;
        log(
          e.data.agent_id,
          roleOf(e.data.agent_id),
          `${verb} ${e.data.target}${tail}`,
          e.data.ok ? "info" : "warn",
        );
        break;
      }
      case "finding.submitted": {
        own("findings")[e.data.finding_id] = {
          id: e.data.finding_id,
          file: e.data.file,
          severity: e.data.severity,
          category: e.data.category,
          title: e.data.title,
          status: "open",
        };
        s.findingsVersion += 1;
        const a = agent(e.data.agent_id);
        if (a) a.findings += 1;
        if (s.analysis) s.analysis = { ...s.analysis, findings: s.analysis.findings + 1 };
        log(
          e.data.agent_id,
          e.data.category,
          `Finding #${e.data.finding_id} [${e.data.severity}] ${e.data.title} (${e.data.file}:${e.data.line_start})`,
          "finding",
        );
        break;
      }
      case "agent.finished": {
        const a = agent(e.data.agent_id);
        if (a)
          Object.assign(a, {
            status: e.data.error ? "failed" : "done",
            findings: e.data.findings,
            rejected: e.data.rejected,
            turns: e.data.turns,
            tokens: e.data.input_tokens + e.data.output_tokens,
            tokensIn: e.data.input_tokens,
            tokensOut: e.data.output_tokens,
            costUsd: e.data.cost_usd ?? null,
            finishedAt: ts,
            error: e.data.error,
          });
        log(
          e.data.agent_id,
          roleOf(e.data.agent_id),
          e.data.error
            ? `Stopped: ${e.data.error}`
            : `Done in ${e.data.turns} turns: ${e.data.findings} findings, ${e.data.rejected} rejected by verification`,
          e.data.error ? "error" : "ok",
        );
        break;
      }
      case "analysis.completed":
        if (s.analysis)
          s.analysis = {
            ...s.analysis,
            status: "done",
            findings: e.data.findings,
            tokensIn: e.data.input_tokens,
            tokensOut: e.data.output_tokens,
            costUsd: e.data.cost_usd ?? null,
            durationS: e.data.duration_s,
          };
        log(
          null,
          "coordinator",
          `Run complete: ${e.data.findings} findings in ${e.data.duration_s.toFixed(0)}s`,
          "ok",
        );
        break;
      case "analysis.failed":
        if (s.analysis) s.analysis = { ...s.analysis, status: "failed", error: e.data.error };
        log(null, "coordinator", `Run failed: ${e.data.error}`, "error");
        break;
      case "finding.updated": {
        const all = own("findings");
        if (all[e.data.finding_id]) all[e.data.finding_id] = { ...all[e.data.finding_id], status: e.data.status };
        s.findingsVersion += 1;
        log(null, null, `Finding #${e.data.finding_id} marked ${e.data.status}`, "info");
        break;
      }
      case "fix.started":
        Object.assign(fix(e.data.fix_id, e.data.finding_id), { status: "running", error: null, startedAt: ts });
        log(`fix-${e.data.fix_id}`, "fixer", `Fixer started on finding #${e.data.finding_id}`, "info");
        break;
      case "fix.progress": {
        const f = fix(e.data.fix_id, e.data.finding_id);
        Object.assign(f, {
          step: e.data.step,
          detail: e.data.detail,
          steps: [...f.steps, { step: e.data.step, detail: e.data.detail, ts }],
        });
        log(`fix-${e.data.fix_id}`, "fixer", `${e.data.step} ${e.data.detail}`, "info");
        break;
      }
      case "fix.completed":
        Object.assign(fix(e.data.fix_id, e.data.finding_id), {
          status: "ready",
          step: null,
          filesChanged: e.data.files_changed,
          testsPassed: e.data.tests_passed,
        });
        log(
          `fix-${e.data.fix_id}`,
          "fixer",
          `Patch ready: ${e.data.files_changed} file(s); tests ${
            e.data.tests_passed == null ? "not run" : e.data.tests_passed ? "passed" : "FAILED"
          }`,
          e.data.tests_passed === false ? "warn" : "ok",
        );
        break;
      case "fix.failed": {
        const f = fix(e.data.fix_id, e.data.finding_id);
        // A failed pull-request attempt leaves the fix itself ready on the server.
        Object.assign(f, { status: f.status === "ready" ? "ready" : "failed", step: null, error: e.data.error });
        log(`fix-${e.data.fix_id}`, "fixer", `Failed: ${e.data.error}`, "error");
        break;
      }
      case "fix.pr_opened":
        Object.assign(fix(e.data.fix_id, e.data.finding_id), { status: "pr_opened", url: e.data.url, error: null });
        log(`fix-${e.data.fix_id}`, "fixer", `Draft pull request opened: ${e.data.url}`, "ok");
        break;
    }
  }
  return s;
}

export function useCityStream(repoId: number | null): CityState {
  const [state, setState] = useState<CityState>(EMPTY);
  const lastSeq = useRef(0);

  useEffect(() => {
    setState(EMPTY);
    lastSeq.current = 0;
    if (repoId == null) return;

    let ws: WebSocket | null = null;
    let closed = false;
    let retry: number | undefined;
    let raf: number | undefined;
    let buffer: Envelope[] = [];

    // Replays arrive as a burst of hundreds of messages; render once per frame.
    const flush = () => {
      raf = undefined;
      const batch = buffer;
      buffer = [];
      if (!batch.length) return;
      setState((prev) => {
        const next = applyEvents(prev, batch, performance.now());
        lastSeq.current = next.lastSeq;
        return next;
      });
    };

    // The stream never "finishes": agents and fixes keep adding events after the city is built,
    // so always reconnect, resuming after the last seq we applied.
    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws/repos/${repoId}?after_seq=${lastSeq.current}`);
      ws.onmessage = (m) => {
        buffer.push(JSON.parse(m.data) as Envelope);
        raf ??= requestAnimationFrame(flush);
      };
      ws.onclose = () => {
        if (!closed) retry = window.setTimeout(connect, 1500);
      };
    };
    connect();

    return () => {
      closed = true;
      if (retry) clearTimeout(retry);
      if (raf) cancelAnimationFrame(raf);
      ws?.close();
    };
  }, [repoId]);

  return state;
}
