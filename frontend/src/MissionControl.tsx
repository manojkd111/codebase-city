import { useEffect, useMemo, useState } from "react";
import { listFixes, openPullRequest, setFindingStatus, startAnalysis, startFix } from "./api";
import { FIXER_COLOR, ROLE_COLOR, SEVERITY_COLOR } from "./Agents3D";
import type { Finding, FindingStatus, Fix, Role, ServerConfig } from "./types";
import type { ActivityEntry, AgentState, CityState, FixState } from "./useCityStream";

// The server sends the billed cost. These list prices only cover events recorded before it did,
// and give an upper bound: they price resent (cached) input at the full rate, ~5x too high for an agent loop.
const PRICE_IN = 4;
const PRICE_OUT = 20;
const SEV_RANK = { critical: 0, high: 1, medium: 2, low: 3 } as const;
const money = (v: number) => (v < 0.01 && v > 0 ? "<$0.01" : `$${v.toFixed(2)}`);
/** Billed cost when the server recorded it, else an upper bound marked with ≤. */
const costText = (x: { costUsd: number | null; tokensIn: number; tokensOut: number }) =>
  x.costUsd != null ? money(x.costUsd) : `≤${money((x.tokensIn * PRICE_IN + x.tokensOut * PRICE_OUT) / 1e6)}`;
const ktok = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 100_000 ? 0 : 1)}k` : `${n}`);

function elapsed(from: number, to: number) {
  const s = Math.max(0, Math.round((to - from) / 1000));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
}

/** Re-render every second while something is running, so timers tick. */
function useNow(active: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [active]);
  return active ? now : Date.now();
}

const VERB: Record<string, string> = {
  read_file: "Reading",
  search_code: "Searching",
  get_file_metrics: "Checking metrics",
  get_imports: "Tracing imports",
  submit_finding: "Submitting finding",
};

type Tab = "agents" | "findings" | "activity";
type Filter = "live" | FindingStatus | "all";

interface Props {
  repoId: number;
  repoName: string;
  city: CityState;
  config: ServerConfig | null;
  findings: Finding[];
  selectedFinding: number | null;
  onSelectFinding: (id: number | null) => void;
  onFocusFile: (path: string) => void;
}

export function MissionControl(p: Props) {
  const [tab, setTab] = useState<Tab>("agents");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const a = p.city.analysis;
  const running = a?.status === "running";
  const fixesRunning = Object.values(p.city.fixes).some((f) => f.status === "running");
  const now = useNow(running || fixesRunning);
  const selected = p.findings.find((f) => f.id === p.selectedFinding) ?? null;
  const liveFindings = p.findings.filter((f) => f.status === "open" || f.status === "confirmed").length;

  const dispatch = async () => {
    setBusy(true);
    setError(null);
    try {
      await startAnalysis(p.repoId);
      setTab("agents");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const noKey = p.config && !p.config.llm_credentials;
  return (
    <div className="mc">
      <header className="mc-head">
        <h2>Mission Control</h2>
        {a && <span className={`pill pill-${a.status === "done" ? "ready" : a.status}`}>{a.status}</span>}
      </header>

      <section className="runbar">
        <button
          className="primary"
          onClick={dispatch}
          disabled={busy || running || p.city.status !== "ready"}
          title={p.city.status !== "ready" ? "Wait for the city to finish building" : undefined}
        >
          {running ? "Agents are working…" : a ? "Run agents again" : "Dispatch agents"}
        </button>
        {a && (
          <div className="stats4">
            <div>
              <dt>Elapsed</dt>
              <dd>{a.durationS != null ? elapsed(0, a.durationS * 1000) : elapsed(a.startedAt, now)}</dd>
            </div>
            <div>
              <dt>Tokens</dt>
              <dd>{ktok(a.tokensIn + a.tokensOut)}</dd>
            </div>
            <div
              title={
                a.costUsd != null
                  ? "Billed cost at list price, with cached input at the cache-read rate"
                  : "Upper bound: recorded before billed cost was tracked"
              }
            >
              <dt>Cost</dt>
              <dd>{costText(a)}</dd>
            </div>
            <div>
              <dt>Found</dt>
              <dd>{a.findings}</dd>
            </div>
          </div>
        )}
        {noKey && (
          <p className="warn">
            The server has no Anthropic credentials. Set <code>ANTHROPIC_API_KEY</code> and restart the API to run agents.
          </p>
        )}
        {error && <p className="error">{error}</p>}
        {a?.status === "failed" && <p className="error">{a.error}</p>}
      </section>

      {selected ? (
        <FindingDetail
          key={selected.id}
          finding={selected}
          repoName={p.repoName}
          config={p.config}
          city={p.city}
          now={now}
          onBack={() => p.onSelectFinding(null)}
          onFocusFile={p.onFocusFile}
        />
      ) : (
        <>
          <nav className="tabs">
            <button className={`tab ${tab === "agents" ? "on" : ""}`} onClick={() => setTab("agents")}>
              Agents
            </button>
            <button className={`tab ${tab === "findings" ? "on" : ""}`} onClick={() => setTab("findings")}>
              Findings{liveFindings ? ` (${liveFindings})` : ""}
            </button>
            <button className={`tab ${tab === "activity" ? "on" : ""}`} onClick={() => setTab("activity")}>
              Activity{p.city.activity.length ? ` (${p.city.activity.length})` : ""}
            </button>
          </nav>
          {tab === "agents" && <AgentTree city={p.city} now={now} onFocusFile={p.onFocusFile} />}
          {tab === "findings" && <FindingList findings={p.findings} onSelect={p.onSelectFinding} />}
          {tab === "activity" && <Activity entries={p.city.activity} />}
        </>
      )}
    </div>
  );
}

// ---- Agents: coordinator -> specialists, plus fixers ----------------------------------------------

function AgentTree({ city, now, onFocusFile }: { city: CityState; now: number; onFocusFile: (p: string) => void }) {
  const agents = Object.values(city.agents);
  const fixes = Object.values(city.fixes).sort((x, y) => y.id - x.id).slice(0, 4);
  const a = city.analysis;
  if (!a && !fixes.length) {
    return (
      <p className="muted small explain">
        Dispatching sends a coordinator over the city's metrics. It assigns hotspot files to three specialists
        (security, tests, debt), which read the code and file findings. Every finding is checked against the source
        before it's stored. You'll see each tool call here as it happens.
      </p>
    );
  }
  return (
    <div className="tree">
      {a && (
        <div className="node root">
          <span className="dot" style={{ background: "#e6eaf0" }} />
          <div className="node-body">
            <div className="agent-top">
              <strong>Coordinator</strong>
              <span className="muted small">
                {agents.length ? `planned · ${agents.length} specialists` : a.status === "running" ? "planning…" : a.status}
              </span>
            </div>
            <span className="muted small">{a.model} · metrics only, never code</span>
          </div>
        </div>
      )}
      {agents.map((ag) => (
        <AgentCard key={ag.id} ag={ag} now={now} onFocusFile={onFocusFile} />
      ))}
      {fixes.length > 0 && (
        <>
          <h4 className="tree-sub">Fixers</h4>
          {fixes.map((f) => (
            <FixerCard key={f.id} fix={f} now={now} />
          ))}
        </>
      )}
    </div>
  );
}

function AgentCard({ ag, now, onFocusFile }: { ag: AgentState; now: number; onFocusFile: (p: string) => void }) {
  const working = ag.status === "working";
  const pct = ag.maxTurns ? Math.min(100, (ag.turns / ag.maxTurns) * 100) : 0;
  return (
    <div className={`node child ${working ? "live" : ""}`}>
      <span className="dot" style={{ background: ROLE_COLOR[ag.role] }} />
      <div className="node-body">
        <div className="agent-top">
          <strong>{ag.role}</strong>
          <span className={`small ${ag.status === "failed" ? "error" : "muted"}`}>
            {working ? "working" : ag.status} · {elapsed(ag.startedAt, ag.finishedAt ?? now)}
          </span>
        </div>
        {working && ag.lastTool ? (
          <div className="now small">
            <span className="spinner" style={{ borderTopColor: ROLE_COLOR[ag.role] }} />
            {VERB[ag.lastTool] ?? ag.lastTool}{" "}
            {ag.path ? (
              <button className="link mono" onClick={() => onFocusFile(ag.path!)}>
                {ag.lastTarget}
              </button>
            ) : (
              <span className="mono">{ag.lastTarget}</span>
            )}
          </div>
        ) : working ? (
          <div className="now small muted">
            <span className="spinner" style={{ borderTopColor: ROLE_COLOR[ag.role] }} />
            Thinking…
          </div>
        ) : null}
        {ag.maxTurns > 0 && (
          <div className="bar thin" title={`turn ${ag.turns} of ${ag.maxTurns}`}>
            <div style={{ width: `${pct}%`, background: ROLE_COLOR[ag.role] }} />
          </div>
        )}
        <div className="muted small meta">
          turn {ag.turns}
          {ag.maxTurns ? `/${ag.maxTurns}` : ""} · {ag.toolCalls} tool calls · {ktok(ag.tokensIn + ag.tokensOut)} tok ·{" "}
          {costText(ag)} · {ag.findings} found
          {ag.rejected > 0 && ` · ${ag.rejected} rejected`}
        </div>
        {!working && ag.reason && <div className="muted small">{ag.reason}</div>}
        {ag.error && <p className="error small">{ag.error}</p>}
      </div>
    </div>
  );
}

const FIX_STAGES = ["Investigating", "Patching", "Testing", "Ready"] as const;

function fixStage(f: FixState): number {
  if (f.status === "ready" || f.status === "pr_opened") return 3;
  if (f.steps.some((s) => s.step === "test")) return 2;
  if (f.steps.some((s) => s.step === "edit")) return 1;
  return 0;
}

function FixerCard({ fix, now }: { fix: FixState; now: number }) {
  const working = fix.status === "running";
  return (
    <div className={`node child ${working ? "live" : ""}`}>
      <span className="dot" style={{ background: FIXER_COLOR }} />
      <div className="node-body">
        <div className="agent-top">
          <strong>fixer · #{fix.findingId}</strong>
          <span className={`small ${fix.status === "failed" ? "error" : "muted"}`}>
            {fix.status.replace("_", " ")}
            {working && ` · ${elapsed(fix.startedAt, now)}`}
          </span>
        </div>
        {working && (
          <div className="now small">
            <span className="spinner" style={{ borderTopColor: FIXER_COLOR }} />
            {FIX_STAGES[fixStage(fix)]} {fix.detail && <span className="mono">{fix.detail}</span>}
          </div>
        )}
      </div>
    </div>
  );
}

// ---- Activity timeline -------------------------------------------------------------------------

const ACTIVITY_FILTERS = ["all", "security", "tests", "debt", "fixer", "findings"] as const;

function Activity({ entries }: { entries: ActivityEntry[] }) {
  const [filter, setFilter] = useState<(typeof ACTIVITY_FILTERS)[number]>("all");
  const shown = useMemo(
    () =>
      entries
        .filter((e) =>
          filter === "all" ? true : filter === "findings" ? e.tone === "finding" : e.role === filter,
        )
        .slice(-200)
        .reverse(),
    [entries, filter],
  );
  if (!entries.length) return <p className="muted small explain">Every agent action will appear here as it happens.</p>;
  return (
    <>
      <div className="chips">
        {ACTIVITY_FILTERS.map((f) => (
          <button key={f} className={`chip ${filter === f ? "on" : ""}`} onClick={() => setFilter(f)}>
            {f !== "all" && f !== "findings" && (
              <span className="dot" style={{ background: f === "fixer" ? FIXER_COLOR : ROLE_COLOR[f as Role] }} />
            )}
            {f}
          </button>
        ))}
      </div>
      <ol className="activity">
        {shown.map((e) => (
          <li key={e.seq} className={`act act-${e.tone}`}>
            <time>
              {new Date(e.ts).toLocaleTimeString([], {
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
                hour12: false,
              })}
            </time>
            <span
              className="dot"
              style={{
                background:
                  e.role === "fixer"
                    ? FIXER_COLOR
                    : e.role && e.role !== "coordinator"
                      ? ROLE_COLOR[e.role]
                      : "#e6eaf0",
              }}
            />
            <span className="act-text">{e.text}</span>
          </li>
        ))}
      </ol>
    </>
  );
}

// ---- Findings ----------------------------------------------------------------------------------

function FindingList({ findings, onSelect }: { findings: Finding[]; onSelect: (id: number) => void }) {
  const [filter, setFilter] = useState<Filter>("live");
  const [category, setCategory] = useState<Role | null>(null);
  const shown = useMemo(
    () =>
      findings
        .filter((f) =>
          filter === "all" ? true : filter === "live" ? f.status === "open" || f.status === "confirmed" : f.status === filter,
        )
        .filter((f) => !category || f.category === category)
        .sort((x, y) => SEV_RANK[x.severity] - SEV_RANK[y.severity] || y.confidence - x.confidence || x.id - y.id),
    [findings, filter, category],
  );
  return (
    <section>
      <div className="chips">
        {(["live", "confirmed", "fixed", "rejected", "all"] as Filter[]).map((f) => (
          <button key={f} className={`chip ${filter === f ? "on" : ""}`} onClick={() => setFilter(f)}>
            {f === "live" ? "to review" : f}
          </button>
        ))}
      </div>
      <div className="chips">
        {(["security", "tests", "debt"] as Role[]).map((c) => (
          <button key={c} className={`chip ${category === c ? "on" : ""}`} onClick={() => setCategory(category === c ? null : c)}>
            <span className="dot" style={{ background: ROLE_COLOR[c] }} /> {c}
          </button>
        ))}
      </div>
      {shown.length === 0 ? (
        <p className="muted small">
          {findings.length === 0 ? "No findings yet. Dispatch the agents to inspect the city." : "Nothing here."}
        </p>
      ) : (
        <ul className="findings">
          {shown.map((f) => (
            <li key={f.id}>
              <button onClick={() => onSelect(f.id)}>
                <span className="sev" style={{ background: SEVERITY_COLOR[f.severity] }}>
                  {f.severity}
                </span>
                <span className="f-body">
                  <span className="f-title">{f.title}</span>
                  <span className="mono small muted">
                    {f.file}:{f.line_start}
                    {f.status !== "open" && ` · ${f.status}`}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function FindingDetail({
  finding: f,
  repoName,
  config,
  city,
  now,
  onBack,
  onFocusFile,
}: {
  finding: Finding;
  repoName: string;
  config: ServerConfig | null;
  city: CityState;
  now: number;
  onBack: () => void;
  onFocusFile: (path: string) => void;
}) {
  const [fixes, setFixes] = useState<Fix[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [confirmPr, setConfirmPr] = useState(false);
  const [busy, setBusy] = useState(false);
  const live = Object.values(city.fixes).filter((x) => x.findingId === f.id);
  const liveKey = live.map((x) => `${x.id}:${x.status}:${x.step}:${x.error}`).join("|");

  useEffect(() => {
    let stale = false;
    listFixes(f.id)
      .then((r) => !stale && setFixes(r))
      .catch(() => {});
    return () => {
      stale = true;
    };
  }, [f.id, liveKey]);

  useEffect(() => {
    onFocusFile(f.file);
  }, [f.file, onFocusFile]);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const fix = fixes[0];
  const liveFix = (fix ? city.fixes[fix.id] : undefined) ?? live.sort((x, y) => y.id - x.id)[0];
  const fixRunning = liveFix?.status === "running" || fix?.status === "running" || fix?.status === "queued";

  return (
    <div className="mc-detail">
      <button className="link" onClick={onBack}>
        ← Back
      </button>
      <section className="detail">
        <div className="tags">
          <span className="sev" style={{ background: SEVERITY_COLOR[f.severity] }}>
            {f.severity}
          </span>
          <span className="pill">
            <span className="dot" style={{ background: ROLE_COLOR[f.category] }} /> {f.category}
          </span>
          <span className="pill">{f.status}</span>
          <span className="pill">{Math.round(f.confidence * 100)}% sure</span>
          {f.needs_human_review && <span className="pill pill-running">needs review</span>}
        </div>
        <h3>{f.title}</h3>
        <button className="link mono small" onClick={() => onFocusFile(f.file)}>
          {f.file}:{f.line_start}
          {f.line_end !== f.line_start && `-${f.line_end}`}
        </button>
        <pre className="code">{f.evidence}</pre>
        <h4>Why it matters</h4>
        <p>{f.explanation}</p>
        <h4>Suggested fix</h4>
        <p>{f.suggested_fix}</p>

        {f.status !== "fixed" && (
          <div className="actions">
            {f.status !== "confirmed" && (
              <button className="primary" disabled={busy} onClick={() => act(() => setFindingStatus(f.id, "confirmed"))}>
                Confirm
              </button>
            )}
            {f.status !== "rejected" && (
              <button disabled={busy} onClick={() => act(() => setFindingStatus(f.id, "rejected"))}>
                Reject
              </button>
            )}
            {f.status !== "open" && (
              <button disabled={busy} onClick={() => act(() => setFindingStatus(f.id, "open"))}>
                Reopen
              </button>
            )}
          </div>
        )}
        {f.status === "open" && (
          <p className="muted small">Confirm this finding to let Claude fix it.</p>
        )}
        {error && <p className="error">{error}</p>}
      </section>

      {(f.status === "confirmed" || f.status === "fixed" || fix || liveFix) && (
        <section className="detail fixbox">
          <h4>Fix</h4>
          {f.status === "confirmed" && !fixRunning && (
            <button className="primary big" disabled={busy} onClick={() => act(() => startFix(f.id))}>
              {fix ? "Fix it again" : "⚡ Fix it"}
            </button>
          )}
          {liveFix && (liveFix.status === "running" || fixRunning) && (
            <FixTracker fix={liveFix} now={now} />
          )}
          {fix && fix.status === "failed" && <p className="error">{fix.error}</p>}
          {fix && (fix.status === "ready" || fix.status === "pr_opened") && (
            <>
              {liveFix && <FixTracker fix={liveFix} now={now} />}
              <p>{fix.summary}</p>
              <p className="small">
                <TestBadge passed={fix.tests_passed} /> · {fix.files_changed} file{fix.files_changed === 1 ? "" : "s"}{" "}
                changed{fix.commit_message && <> · <span className="mono">{fix.commit_message}</span></>}
              </p>
              <Diff text={fix.diff} />
              {fix.test_output && (
                <details>
                  <summary className="small">Test output</summary>
                  <pre className="code small">{fix.test_output.slice(-4000)}</pre>
                </details>
              )}
              {fix.status === "pr_opened" && fix.pr_url ? (
                <a className="primary-link" href={fix.pr_url} target="_blank" rel="noreferrer">
                  View draft pull request ↗
                </a>
              ) : confirmPr ? (
                <div className="confirm">
                  <p className="small">
                    This pushes a branch and opens a <strong>draft</strong> pull request on{" "}
                    <strong>github.com/{repoName}</strong>, visible to its maintainers. Continue?
                  </p>
                  <div className="actions">
                    <button
                      className="primary"
                      disabled={busy}
                      onClick={() => act(() => openPullRequest(fix.id)).then(() => setConfirmPr(false))}
                    >
                      Open draft pull request
                    </button>
                    <button onClick={() => setConfirmPr(false)}>Cancel</button>
                  </div>
                </div>
              ) : (
                <>
                  <button
                    className="primary"
                    disabled={!config?.pull_requests || f.status !== "confirmed"}
                    onClick={() => setConfirmPr(true)}
                  >
                    Approve & open draft PR…
                  </button>
                  {config && !config.pull_requests && (
                    <p className="muted small">
                      Set <code>GITHUB_TOKEN</code> on the server to open pull requests.
                    </p>
                  )}
                </>
              )}
              {liveFix?.error && fix.status === "ready" && <p className="error">{liveFix.error}</p>}
            </>
          )}
        </section>
      )}
    </div>
  );
}

function FixTracker({ fix, now }: { fix: FixState; now: number }) {
  const stage = fixStage(fix);
  const failed = fix.status === "failed";
  return (
    <div className="tracker">
      <ol>
        {FIX_STAGES.map((name, i) => (
          <li key={name} className={i < stage ? "done" : i === stage ? (failed ? "failed" : "current") : ""}>
            <span className="step-dot" />
            {name}
          </li>
        ))}
      </ol>
      {fix.status === "running" && (
        <p className="muted small">
          {elapsed(fix.startedAt, now)}
          {fix.detail ? ` · ${fix.detail}` : ""}
        </p>
      )}
    </div>
  );
}

function TestBadge({ passed }: { passed: boolean | null }) {
  if (passed === true) return <span className="ok">tests passed</span>;
  if (passed === false) return <span className="error">tests failed</span>;
  return <span className="muted">tests not run</span>;
}

function Diff({ text }: { text: string }) {
  const lines = text.split("\n").slice(0, 400);
  return (
    <pre className="code diff small">
      {lines.map((l, i) => (
        <div
          key={i}
          className={
            l.startsWith("+++") || l.startsWith("---")
              ? "d-file"
              : l.startsWith("+")
                ? "d-add"
                : l.startsWith("-")
                  ? "d-del"
                  : l.startsWith("@@")
                    ? "d-hunk"
                    : undefined
          }
        >
          {l || " "}
        </div>
      ))}
    </pre>
  );
}
