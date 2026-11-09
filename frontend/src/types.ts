// Mirrors backend/events/schema.py. Keep the two in sync when a payload changes.

export interface Repo {
  id: number;
  source: string;
  name: string | null;
  commit_sha: string | null;
  status: "queued" | "running" | "ready" | "failed" | string;
  error: string | null;
  file_count: number | null;
  stats: Record<string, unknown> | null;
  created_at: string;
}

export interface District {
  name: string;
  file_count: number;
  total_bytes: number;
  /** folder (posix dirname, "" for repo root) -> file count; absent on pre-Week-2 events */
  folders?: Record<string, number>;
}

export interface Building {
  path: string;
  district: string;
  language: string;
  size_bytes: number;
  loc: number;
  functions: number;
  complexity_total: number;
  complexity_max: number;
  churn_30: number;
  churn_90: number;
  coverage: number | null;
  risk: number;
  parse_error: boolean;
  is_test: boolean;
}

export interface Road {
  src: string;
  dst: string;
  imports: number;
}

export interface IngestProgress {
  stage: string;
  done: number;
  total: number;
  detail: Record<string, unknown>;
}

export interface IngestCompleted {
  commit_sha: string | null;
  files: number;
  districts: number;
  roads: number;
  edges: number;
  duration_s: number;
  skipped: Record<string, number>;
  truncated: boolean;
}

export interface Finding {
  id: number;
  repo_id: number;
  run_id: number | null;
  agent_id: string;
  category: Role;
  severity: Severity;
  file: string;
  line_start: number;
  line_end: number;
  title: string;
  evidence: string;
  explanation: string;
  suggested_fix: string;
  confidence: number;
  needs_human_review: boolean;
  status: FindingStatus;
  created_at: string;
}

export interface Fix {
  id: number;
  finding_id: number;
  repo_id: number;
  status: "queued" | "running" | "ready" | "failed" | "pr_opened";
  summary: string;
  commit_message: string;
  diff: string;
  files_changed: number;
  tests_passed: boolean | null;
  test_output: string;
  pr_url: string | null;
  error: string | null;
  input_tokens: number;
  output_tokens: number;
  created_at: string;
}

export interface ServerConfig {
  model: string;
  llm_credentials: boolean;
  pull_requests: boolean;
  workers: boolean;
}

export type Role = "security" | "tests" | "debt";
export type Severity = "critical" | "high" | "medium" | "low";
export type FindingStatus = "open" | "confirmed" | "rejected" | "fixed";

export type CityEvent =
  | { type: "ingest.started"; data: { source: string } }
  | { type: "ingest.progress"; data: IngestProgress }
  | { type: "city.district_added"; data: District }
  | { type: "city.building_added"; data: Building }
  | { type: "city.road_added"; data: Road }
  | { type: "ingest.completed"; data: IngestCompleted }
  | { type: "ingest.failed"; data: { error: string } }
  | { type: "analysis.started"; data: { run_id: number; model: string } }
  | { type: "agent.dispatched"; data: { run_id: number; agent_id: string; role: Role; files: string[]; reason: string } }
  | { type: "agent.moved"; data: { run_id: number; agent_id: string; path: string; action: string } }
  | {
      type: "finding.submitted";
      data: {
        run_id: number;
        agent_id: string;
        finding_id: number;
        category: Role;
        severity: Severity;
        file: string;
        line_start: number;
        line_end: number;
        title: string;
        confidence: number;
      };
    }
  | {
      type: "agent.turn";
      data: {
        run_id: number;
        agent_id: string;
        turn: number;
        max_turns: number;
        input_tokens: number;
        output_tokens: number;
        tool_calls: number;
        duration_ms: number;
        // Absent on events stored before billed cost was tracked.
        cache_read_tokens?: number;
        cost_usd?: number | null;
      };
    }
  | {
      type: "agent.tool_call";
      data: {
        run_id: number;
        agent_id: string;
        tool: string;
        target: string;
        ok: boolean;
        duration_ms: number;
        note: string;
      };
    }
  | {
      type: "agent.finished";
      data: {
        run_id: number;
        agent_id: string;
        findings: number;
        rejected: number;
        turns: number;
        input_tokens: number;
        output_tokens: number;
        error: string | null;
        cache_read_tokens?: number;
        cost_usd?: number | null;
      };
    }
  | {
      type: "analysis.completed";
      data: {
        run_id: number;
        findings: number;
        duration_s: number;
        input_tokens: number;
        output_tokens: number;
        cache_read_tokens?: number;
        cost_usd?: number | null;
      };
    }
  | { type: "analysis.failed"; data: { run_id: number; error: string } }
  | { type: "finding.updated"; data: { finding_id: number; status: FindingStatus } }
  | { type: "fix.started"; data: { fix_id: number; finding_id: number } }
  | { type: "fix.progress"; data: { fix_id: number; finding_id: number; step: string; detail: string } }
  | {
      type: "fix.completed";
      data: { fix_id: number; finding_id: number; files_changed: number; tests_passed: boolean | null };
    }
  | { type: "fix.failed"; data: { fix_id: number; finding_id: number; error: string } }
  | { type: "fix.pr_opened"; data: { fix_id: number; finding_id: number; url: string } };

export type Envelope = CityEvent & { repo_id: number; seq: number; ts: string };

export interface ImportNeighbors {
  path: string;
  imports: string[];
  imported_by: string[];
}
