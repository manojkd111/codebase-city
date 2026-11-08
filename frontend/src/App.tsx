import { useCallback, useEffect, useMemo, useState } from "react";
import { createRepo, getConfig, listFindings, listRepos } from "./api";
import { City } from "./City";
import { Inspector } from "./Inspector";
import { MissionControl } from "./MissionControl";
import type { Finding, ImportNeighbors, Repo, ServerConfig } from "./types";
import { useCityStream } from "./useCityStream";

const repoFromHash = () => {
  const m = location.hash.match(/^#\/repos\/(\d+)/);
  return m ? Number(m[1]) : null;
};

export default function App() {
  const [repos, setRepos] = useState<Repo[]>([]);
  const [repoId, setRepoId] = useState<number | null>(repoFromHash);
  const [hovered, setHovered] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const [config, setConfig] = useState<ServerConfig | null>(null);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [selectedFinding, setSelectedFinding] = useState<number | null>(null);
  const [trace, setTrace] = useState<ImportNeighbors | null>(null);
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      return localStorage.getItem("cbc.sidebar") === "collapsed";
    } catch {
      return false;
    }
  });
  const toggleSidebar = () => {
    setCollapsed((c) => {
      try {
        localStorage.setItem("cbc.sidebar", c ? "open" : "collapsed");
      } catch {
        /* per-viewer convenience only */
      }
      return !c;
    });
  };
  const city = useCityStream(repoId);

  useEffect(() => {
    getConfig().then(setConfig).catch(() => {});
  }, []);
  // Events carry finding summaries; the full text (evidence, explanation) comes from the API.
  // Refetch whenever the stream says a finding or fix changed, debounced across bursts.
  useEffect(() => {
    if (repoId == null) return;
    let stale = false;
    const t = setTimeout(() => {
      listFindings(repoId)
        .then((r) => !stale && setFindings(r))
        .catch(() => {});
    }, 250);
    return () => {
      stale = true;
      clearTimeout(t);
    };
  }, [repoId, city.findingsVersion]);

  const refresh = useCallback(() => listRepos().then(setRepos).catch(() => {}), []);
  useEffect(() => {
    refresh();
  }, [refresh]);
  // Status pills in the list follow the stream for the open repo, and poll for others.
  useEffect(() => {
    if (!repos.some((r) => r.status === "queued" || r.status === "running")) return;
    const t = setInterval(refresh, 2500);
    return () => clearInterval(t);
  }, [repos, refresh]);
  useEffect(() => {
    if (city.status === "ready" || city.status === "failed") refresh();
  }, [city.status, refresh]);

  useEffect(() => {
    const onHash = () => setRepoId(repoFromHash());
    addEventListener("hashchange", onHash);
    return () => removeEventListener("hashchange", onHash);
  }, []);
  useEffect(() => {
    if (repoId == null && repos.length) location.hash = `#/repos/${repos[0].id}`;
  }, [repoId, repos]);
  useEffect(() => {
    setSelected(null);
    setHovered(null);
    setFocus(null);
    setSelectedFinding(null);
    setFindings([]);
    setTrace(null);
  }, [repoId]);
  // A trace belongs to one building; selecting another clears it.
  useEffect(() => {
    setTrace((t) => (t && t.path !== selected ? null : t));
  }, [selected]);

  const repo = repos.find((r) => r.id === repoId) ?? null;
  const maxRisk = useMemo(() => city.buildings.reduce((m, b) => Math.max(m, b.risk), 0), [city.buildings]);
  const hotspots = useMemo(
    () =>
      city.buildings
        .filter((b) => !b.is_test)
        .sort((a, b) => b.risk - a.risk)
        .slice(0, 10),
    [city.buildings],
  );
  const selectedBuilding = city.buildings.find((b) => b.path === selected) ?? null;

  const select = (path: string | null, fly = false) => {
    setSelected(path);
    if (fly) setFocus(path);
  };
  const focusFile = useCallback((path: string) => {
    setSelected(path);
    setFocus(path);
  }, []);
  const fileFindings = findings.filter((f) => f.file === selected && f.status !== "rejected");

  return (
    <div className={`app ${collapsed ? "collapsed" : ""}`}>
      {collapsed ? (
        <aside className="rail">
          <button className="icon rail-btn" onClick={toggleSidebar} title="Show repositories" aria-label="Show sidebar">
            »
          </button>
          <span className="brand-mark rail-mark" aria-hidden>
            ▟▙
          </span>
        </aside>
      ) : (
      <aside className="panel">
        <header className="brand">
          <span className="brand-mark" aria-hidden>
            ▟▙
          </span>
          <h1>Codebase City</h1>
          <button className="icon collapse" onClick={toggleSidebar} title="Hide sidebar" aria-label="Hide sidebar">
            «
          </button>
        </header>

        <NewRepoForm
          onCreated={(id) => {
            refresh();
            location.hash = `#/repos/${id}`;
          }}
        />

        <section>
          <h2>Cities</h2>
          {repos.length === 0 && <p className="muted">No repos yet. Paste a GitHub URL above.</p>}
          <ul className="repo-list">
            {repos.map((r) => (
              <li key={r.id}>
                <a href={`#/repos/${r.id}`} className={r.id === repoId ? "active" : undefined}>
                  <span className="repo-name">{r.name ?? r.source.replace("https://github.com/", "")}</span>
                  <span className={`pill pill-${r.status}`}>{r.status}</span>
                </a>
              </li>
            ))}
          </ul>
        </section>

        {repoId != null && (
          <section>
            <h2>{repo?.name ?? `Repo #${repoId}`}</h2>
            <Status city={city} />
            {city.completed && (
              <dl className="stats">
                <div>
                  <dt>Files</dt>
                  <dd>{city.completed.files}</dd>
                </div>
                <div>
                  <dt>Districts</dt>
                  <dd>{city.completed.districts}</dd>
                </div>
                <div>
                  <dt>Imports</dt>
                  <dd>{city.completed.edges}</dd>
                </div>
                <div>
                  <dt>Commit</dt>
                  <dd className="mono">{city.completed.commit_sha?.slice(0, 7) ?? "n/a"}</dd>
                </div>
              </dl>
            )}
          </section>
        )}

        {hotspots.length > 0 && (
          <section>
            <h2>Hotspots</h2>
            <ol className="hotspots">
              {hotspots.map((b) => (
                <li key={b.path}>
                  <button
                    className={b.path === selected ? "active" : undefined}
                    onClick={() => select(b.path, true)}
                    onMouseEnter={() => setHovered(b.path)}
                    onMouseLeave={() => setHovered(null)}
                    title={b.path}
                  >
                    <span className="hs-path">{b.path}</span>
                    <span className="hs-risk">{b.risk.toFixed(1)}</span>
                  </button>
                </li>
              ))}
            </ol>
          </section>
        )}

        {city.buildings.length > 0 && <Legend />}
      </aside>
      )}

      <main className="stage">
        {repoId == null ? (
          <div className="empty">Pick a city, or build one from a GitHub URL.</div>
        ) : city.districts.length === 0 ? (
          <div className="empty">{city.status === "failed" ? "Ingest failed." : "Surveying the land…"}</div>
        ) : (
          <City
            districts={city.districts}
            buildings={city.buildings}
            roads={city.roads}
            maxRisk={maxRisk}
            hovered={hovered}
            selected={selected}
            onHover={setHovered}
            onSelect={(p) => select(p)}
            focus={focus}
            agents={city.agents}
            findings={city.findings}
            fixes={city.fixes}
            trace={trace}
          />
        )}
        {repo && city.completed && (
          <div className="hud">
            <strong>{repo.name}</strong>
            <span>{city.completed.files} files</span>
            <span>{city.completed.districts} districts</span>
            <span>{city.completed.edges} imports</span>
            {trace && (
              <span className="hud-trace">
                <span className="dot" style={{ background: "#5ad1ff" }} /> imports {trace.imports.length}
                <span className="dot" style={{ background: "#ffb35a" }} /> imported by {trace.imported_by.length}
              </span>
            )}
          </div>
        )}
        {selectedBuilding && repoId != null && (
          <Inspector
            key={selectedBuilding.path}
            repoId={repoId}
            repoSource={repo?.source ?? null}
            commitSha={repo?.commit_sha ?? null}
            building={selectedBuilding}
            findings={fileFindings}
            trace={trace}
            onTrace={setTrace}
            onOpenFinding={setSelectedFinding}
            onFocusFile={focusFile}
            onClose={() => select(null)}
          />
        )}
        <p className="hint">Drag to orbit · right-drag to pan · scroll to zoom · click a building to inspect it</p>
      </main>

      {repoId != null && (
        <aside className="panel mc-panel">
          <MissionControl
            repoId={repoId}
            repoName={repo?.name ?? ""}
            city={city}
            config={config}
            findings={findings}
            selectedFinding={selectedFinding}
            onSelectFinding={setSelectedFinding}
            onFocusFile={focusFile}
          />
        </aside>
      )}
    </div>
  );
}

function NewRepoForm({ onCreated }: { onCreated: (id: number) => void }) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <form
      className="new-repo"
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setError(null);
        try {
          const { id } = await createRepo(url.trim());
          setUrl("");
          onCreated(id);
        } catch (err) {
          setError((err as Error).message);
        } finally {
          setBusy(false);
        }
      }}
    >
      <label htmlFor="repo-url">GitHub repo</label>
      <div className="row">
        <input
          id="repo-url"
          type="url"
          required
          placeholder="https://github.com/owner/repo"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
        />
        <button type="submit" disabled={busy}>
          {busy ? "…" : "Build"}
        </button>
      </div>
      {error && <p className="error">{error}</p>}
    </form>
  );
}

function Status({ city }: { city: ReturnType<typeof useCityStream> }) {
  if (city.status === "failed") return <p className="error">{city.error}</p>;
  if (city.status === "ready") {
    return (
      <p className="muted">
        Built in {city.completed?.duration_s.toFixed(1)}s
        {city.completed?.truncated ? " (truncated: repo exceeded the file limit)" : ""}
      </p>
    );
  }
  const total = city.districts.reduce((s, d) => s + d.file_count, 0);
  const pct = total ? Math.round((100 * city.buildings.length) / total) : 0;
  return (
    <div className="progress">
      <p className="muted">
        {city.status === "connecting" ? "Connecting…" : `Stage: ${city.progress?.stage ?? "starting"}`}
        {total > 0 && ` · ${city.buildings.length}/${total} buildings`}
      </p>
      <div className="bar">
        <div style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function Legend() {
  return (
    <section className="legend">
      <h2>Reading the city</h2>
      <div className="ramp" aria-hidden />
      <div className="ramp-labels">
        <span>low risk</span>
        <span>high risk</span>
      </div>
      <ul>
        <li>Height = cyclomatic complexity</li>
        <li>Footprint = file size</li>
        <li>Grey buildings = tests</li>
        <li>Arcs = imports between districts</li>
      </ul>
    </section>
  );
}
