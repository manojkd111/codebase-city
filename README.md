# Codebase City

Paste a repo URL. Watch it become a city. Watch AI drones find the bugs. Click one and get a working PR.

> **Status:** all six phases are built and tested against a scripted Claude client. Live agent runs
> need `ANTHROPIC_API_KEY`; sandboxed test runs need Docker; pull requests need `GITHUB_TOKEN`.

## How it works

1. **Ingest (deterministic).** Clone, walk, measure: tree-sitter imports, lizard complexity, git churn.
   Every file gets a hotspot `risk` score. No model is involved.
2. **City.** The browser lays out districts (top-level packages), folder blocks and buildings from the
   event stream and raises them live. Height = cyclomatic complexity, footprint = size, colour = risk.
3. **Agents.** A coordinator assigns hotspot files to three specialists (security, tests, debt) from
   metrics alone. The specialists read code through path-safe tools and submit findings. Each
   finding's quoted evidence is checked against the file before it is stored.
4. **Mission Control.** Drones fly to whatever file each agent is reading. The panel shows the agent
   hierarchy, each agent's current tool call, turns, tokens and cost, and a timeline of every event,
   all of it live from the backend. Findings appear as beacons coloured by severity. A human confirms
   or rejects each one.
   Click any building to inspect it: metrics, source at the ingested commit (finding lines
   highlighted), findings, and import neighbours; **Trace dependencies** draws them in the city.
5. **Fix.** For a confirmed finding, a fix agent edits a throwaway checkout. The repo's tests run in a
   Docker sandbox with no network and no secrets. The building glows while the fix runs. You see the
   diff and the test result, then choose whether to open a **draft** pull request (to a fork when you
   can't push upstream).

| City element | Source | Status |
|---|---|---|
| District / folder block | top-level package / directory | ✅ |
| Building height / footprint | cyclomatic complexity (lizard) / file size | ✅ |
| Building colour | hotspot risk (complexity × churn × coverage) | ✅ |
| Roads | import graph via tree-sitter (Python, JS, TS), rolled up per district | ✅ |
| Drones | `agent.moved` events: the file each agent is reading | ✅ |
| Beacons | verified findings, coloured by worst severity; green once fixed | ✅ |
| Coverage | `pytest --cov` in the sandbox (Python repos) | ✅ needs Docker |
| Glowing windows | git churn (computed, not drawn yet) | – |

## Quick start (Windows paths; use `.venv/bin/` on macOS/Linux)

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev,workers]"
.venv/Scripts/python -m pytest -q

set ANTHROPIC_API_KEY=...        # to run agents; optional for browsing cities
set GITHUB_TOKEN=...             # only to open pull requests
.venv/Scripts/python -m uvicorn backend.api.main:app --reload

# second terminal: the city at http://localhost:5173
cd frontend && npm install && npm run dev
```

Optional:
- **Workers:** `docker compose up -d redis`, set `REDIS_URL=redis://localhost:6379` for the API and run
  `.venv/Scripts/python -m arq backend.worker.WorkerSettings`. Ingest, agents and fixes then run in
  worker processes, and their events reach the browser through Redis.
- **Postgres:** `docker compose up -d postgres`, `pip install -e ".[postgres]"`,
  `DATABASE_URL=postgresql+psycopg://city:city@localhost:5432/city`.
- **Sandbox off:** `CBC_SANDBOX=off` never runs repo tests; fixes are then marked "tests not run".

## MCP servers

Three stdio MCP servers expose the same data and the same human-gated actions to any MCP client.
`.mcp.json` registers them for Claude Code.

| Server | Tools |
|---|---|
| `mcp_servers.repo_metrics` | `list_repos`, `get_hotspots`, `get_file_metrics`, `get_import_neighbors`, `get_city_summary` (read-only) |
| `mcp_servers.findings` | `list_findings`, `get_finding`, `set_finding_status` |
| `mcp_servers.github` | `list_fixes`, `get_fix`, `open_pull_request` (confirmed finding + ready fix only) |

## Evals

```bash
.venv/Scripts/python -m evals.benchmark.run --metrics-only   # free: do planted bugs rank as hotspots?
.venv/Scripts/python -m evals.benchmark.run                  # agents vs 13 planted problems (costs money)
.venv/Scripts/python -m evals.tool_selection.run             # does Claude pick the right MCP tool?
```

## API

| | |
|---|---|
| `POST /api/repos {"url"}` | ingest a public GitHub repo |
| `WS /ws/repos/{id}?after_seq=N` | replay stored events after N, then stream live ones |
| `GET /api/repos/{id}/city`, `/hotspots` | snapshot / riskiest files |
| `POST /api/repos/{id}/analyze` | dispatch the agents; `GET .../analysis` for the latest run |
| `GET /api/repos/{id}/findings`, `PATCH /api/findings/{id}` | list / triage findings |
| `POST /api/findings/{id}/fix` | generate a fix (finding must be confirmed) |
| `GET /api/fixes/{id}`, `POST /api/fixes/{id}/pr` | diff + tests / open the pull request |
| `POST /api/repos/{id}/coverage` | collect coverage in the sandbox |
| `GET /api/config` | what this server can do (credentials, PRs, workers) |

## Design decisions
See [docs/decisions](docs/decisions) and [docs/architecture.md](docs/architecture.md).
