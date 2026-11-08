// The building inspector: everything about one file, floating over the city.
// Source comes straight from GitHub at the exact commit that was ingested, so line numbers match findings.

import { useEffect, useMemo, useRef, useState } from "react";
import { getImports } from "./api";
import { SEVERITY_COLOR } from "./Agents3D";
import type { Building, Finding, ImportNeighbors } from "./types";

type Tab = "overview" | "source" | "findings" | "deps";
const MAX_SOURCE_BYTES = 400_000;

interface Props {
  repoId: number;
  repoSource: string | null;
  commitSha: string | null;
  building: Building;
  findings: Finding[];
  trace: ImportNeighbors | null;
  onTrace: (t: ImportNeighbors | null) => void;
  onOpenFinding: (id: number) => void;
  onFocusFile: (path: string) => void;
  onClose: () => void;
}

export function Inspector(p: Props) {
  const [tab, setTab] = useState<Tab>("overview");
  const [deps, setDeps] = useState<ImportNeighbors | null>(null);
  const b = p.building;
  const tracing = p.trace?.path === b.path;

  useEffect(() => {
    let stale = false;
    setDeps(null);
    getImports(p.repoId, b.path)
      .then((d) => !stale && setDeps(d))
      .catch(() => {});
    return () => {
      stale = true;
    };
  }, [p.repoId, b.path]);

  const live = p.findings.filter((f) => f.status !== "rejected");
  const rows: [string, string | number][] = [
    ["District", b.district],
    ["Language", b.language],
    ["Lines of code", b.loc],
    ["Functions", b.functions],
    ["Complexity (total / max)", `${b.complexity_total} / ${b.complexity_max}`],
    ["Commits (30d / 90d)", `${b.churn_30} / ${b.churn_90}`],
    ["Coverage", b.coverage == null ? "unknown" : `${Math.round(b.coverage * 100)}%`],
    ["Size", `${(b.size_bytes / 1024).toFixed(1)} KB`],
    ["Hotspot risk", b.risk.toFixed(2)],
  ];

  return (
    <div className="inspector">
      <header className="insp-head">
        <div className="insp-title">
          <span className="mono">{b.path}</span>
          <span className="insp-tags">
            {b.is_test && <span className="pill">test</span>}
            {b.parse_error && <span className="pill pill-failed">parse error</span>}
            {live.length > 0 && <span className="pill pill-running">{live.length} finding{live.length > 1 ? "s" : ""}</span>}
          </span>
        </div>
        <button className="icon" onClick={p.onClose} aria-label="Close inspector">
          ×
        </button>
      </header>
      <nav className="tabs">
        {(
          [
            ["overview", "Overview"],
            ["source", "Source"],
            ["findings", `Findings${live.length ? ` (${live.length})` : ""}`],
            ["deps", `Dependencies${deps ? ` (${deps.imports.length + deps.imported_by.length})` : ""}`],
          ] as [Tab, string][]
        ).map(([t, label]) => (
          <button key={t} className={`tab ${tab === t ? "on" : ""}`} onClick={() => setTab(t)}>
            {label}
          </button>
        ))}
      </nav>

      <div className="insp-body">
        {tab === "overview" && (
          <>
            <dl className="kv">
              {rows.map(([k, v]) => (
                <div key={k}>
                  <dt>{k}</dt>
                  <dd>{v}</dd>
                </div>
              ))}
            </dl>
            <div className="actions">
              <button
                className={tracing ? "primary" : undefined}
                disabled={!deps}
                onClick={() => p.onTrace(tracing ? null : deps)}
              >
                {tracing ? "Stop tracing" : "Trace dependencies"}
              </button>
              <button onClick={() => setTab("source")}>View source</button>
            </div>
          </>
        )}
        {tab === "source" && (
          <Source
            source={p.repoSource}
            sha={p.commitSha}
            path={b.path}
            findings={live}
            onOpenFinding={p.onOpenFinding}
          />
        )}
        {tab === "findings" &&
          (live.length === 0 ? (
            <p className="muted small">No findings for this file. Dispatch the agents to inspect it.</p>
          ) : (
            <ul className="findings">
              {live.map((f) => (
                <li key={f.id}>
                  <button onClick={() => p.onOpenFinding(f.id)}>
                    <span className="sev" style={{ background: SEVERITY_COLOR[f.severity] }}>
                      {f.severity}
                    </span>
                    <span className="f-body">
                      <span className="f-title">{f.title}</span>
                      <span className="mono small muted">
                        line {f.line_start}
                        {f.status !== "open" && ` · ${f.status}`}
                      </span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ))}
        {tab === "deps" && (
          <>
            <div className="actions" style={{ marginTop: 0 }}>
              <button
                className={tracing ? "primary" : undefined}
                disabled={!deps}
                onClick={() => p.onTrace(tracing ? null : deps)}
              >
                {tracing ? "Stop tracing" : "Trace in the city"}
              </button>
            </div>
            {!deps ? (
              <p className="muted small">Loading…</p>
            ) : (
              <>
                <DepList title="Imports" color="#5ad1ff" paths={deps.imports} onPick={p.onFocusFile} />
                <DepList title="Imported by" color="#ffb35a" paths={deps.imported_by} onPick={p.onFocusFile} />
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function DepList({
  title,
  color,
  paths,
  onPick,
}: {
  title: string;
  color: string;
  paths: string[];
  onPick: (p: string) => void;
}) {
  return (
    <section className="deps">
      <h4>
        <span className="dot" style={{ background: color }} /> {title} ({paths.length})
      </h4>
      {paths.length === 0 ? (
        <p className="muted small">None inside this repository.</p>
      ) : (
        <ul>
          {paths.map((p) => (
            <li key={p}>
              <button className="link mono small" onClick={() => onPick(p)}>
                {p}
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function githubUrls(source: string | null, sha: string | null, path: string) {
  const m = source?.match(/^https:\/\/github\.com\/([^/]+)\/([^/]+?)(?:\.git)?\/?$/);
  if (!m || !sha) return null;
  const rel = path.split("/").map(encodeURIComponent).join("/");
  return {
    raw: `https://raw.githubusercontent.com/${m[1]}/${m[2]}/${sha}/${rel}`,
    blob: `https://github.com/${m[1]}/${m[2]}/blob/${sha}/${rel}`,
  };
}

function Source({
  source,
  sha,
  path,
  findings,
  onOpenFinding,
}: {
  source: string | null;
  sha: string | null;
  path: string;
  findings: Finding[];
  onOpenFinding: (id: number) => void;
}) {
  const urls = githubUrls(source, sha, path);
  const url = urls?.raw ?? null;
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const firstHit = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!url) return;
    let stale = false;
    setText(null);
    setError(null);
    fetch(url)
      .then(async (r) => {
        if (!r.ok) throw new Error(`GitHub returned ${r.status}`);
        const len = Number(r.headers.get("content-length") ?? 0);
        if (len > MAX_SOURCE_BYTES) throw new Error("File is too large to show here.");
        return r.text();
      })
      .then((t) => !stale && setText(t))
      .catch((e) => !stale && setError((e as Error).message));
    return () => {
      stale = true;
    };
  }, [url]);

  // line -> findings covering it
  const marks = useMemo(() => {
    const m = new Map<number, Finding[]>();
    for (const f of findings)
      for (let l = f.line_start; l <= f.line_end; l++) m.set(l, [...(m.get(l) ?? []), f]);
    return m;
  }, [findings]);

  useEffect(() => {
    firstHit.current?.scrollIntoView({ block: "center" });
  }, [text]);

  if (!url) return <p className="muted small">Source is only available for GitHub repositories.</p>;
  if (error) return <p className="error small">{error}</p>;
  if (text == null) return <p className="muted small">Loading from GitHub…</p>;
  const lines = text.split("\n");
  let marked = false;
  return (
    <div className="source">
      <div className="muted small source-meta">
        {lines.length} lines · commit <span className="mono">{sha?.slice(0, 7)}</span>
        <a href={urls!.blob} target="_blank" rel="noreferrer">
          {" "}
          open on GitHub ↗
        </a>
      </div>
      <pre className="code source-code">
        {lines.map((line, i) => {
          const n = i + 1;
          const hit = marks.get(n);
          const isFirst = hit && !marked;
          if (hit) marked = true;
          return (
            <div
              key={n}
              ref={isFirst ? firstHit : undefined}
              className={hit ? "src-line hit" : "src-line"}
              style={hit ? { borderLeftColor: SEVERITY_COLOR[hit[0].severity] } : undefined}
              title={hit?.map((f) => f.title).join("\n")}
              onClick={hit ? () => onOpenFinding(hit[0].id) : undefined}
            >
              <span className="ln">{n}</span>
              {line || " "}
            </div>
          );
        })}
      </pre>
    </div>
  );
}
