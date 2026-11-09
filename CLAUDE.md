# Codebase City: rules for working on this repo

Codebase City turns a GitHub repo into a 3D city; Claude agents inspect it and open PRs.
Build plan: 6 phases over ~8 weeks. Current phase: **all phases built; tuning against live runs**.
Reviews run on Sonnet 5.5 and fixes on Opus 5.5 (ADR 0009). Two planted-bug fixtures: `shop` is
saturated (13/13, ~100% precision) and guards against regressions; `ledger` is the one to improve
(9/14 at baseline, 12/14 with the coverage floor; 100% precision, 0/4 decoys). Ledger runs 5-6 fixed
the CSV-injection and mock-only-test misses but lost others to security's slot limit; the follow-up
(security +2 slots, tests-only files still get a bug reviewer, split on " and ") is not yet measured
live: run `--fixture ledger` twice and `shop` once before building on it.

## Layout
- `backend/ingest/`: deterministic analysis (clone, walk, tree-sitter, lizard, git churn, import graph)
- `backend/events/`: the typed event contract (`schema.py`), pub/sub (`bus.py`), Redis relay (`relay.py`)
- `backend/db/`: SQLAlchemy models (SQLite locally, Postgres in deploy)
- `backend/api/`: FastAPI + WebSocket event stream; `backend/views.py` holds the JSON shapes shared with MCP
- `backend/agents/`: coordinator + specialists, path-safe `Workspace`, verified `submit_finding` (ADR 0005)
- `backend/fixer/`: fix agent, Docker sandbox, GitHub PRs (ADR 0006)
- `backend/jobs.py`, `backend/worker.py`: in-process threads, or arq workers when `REDIS_URL` is set (ADR 0007)
- `mcp_servers/`: three stdio MCP servers (metrics, findings, github); registered in `.mcp.json`
- `evals/`: planted-bug benchmark and MCP tool-selection eval
- `frontend/`: Vite + React Three Fiber city and Mission Control; built only from the event stream (ADR 0004)
- `docs/decisions/`: one ADR per major design choice; add one when you make one

## Commands (Windows, from repo root)
- Tests: `.venv/Scripts/python -m pytest -q` (no API key, Docker or network needed; Claude is faked in `tests/fakes.py`)
- Lint/format: `.venv/Scripts/python -m ruff check . && .venv/Scripts/python -m ruff format .`
- Ingest from CLI: `.venv/Scripts/python -m backend.cli ingest https://github.com/pallets/flask`
- API: `.venv/Scripts/python -m uvicorn backend.api.main:app --reload`
- Frontend (from `frontend/`): `npm run dev` (http://localhost:5173), `npm test`, `npm run typecheck`
  (`CBC_API=http://localhost:8001 npx vite --port 5174` points a second dev server at another API)
- Benchmark: `.venv/Scripts/python -m evals.benchmark.run [--fixture shop|ledger] [--metrics-only]`
  (`--rescore <run.json>` re-scores a saved run for free)

## Rules
- Deterministic code counts; Claude judges. Never send something to a model that a parser can compute.
- All Claude API calls go through `backend/agents/llm.py:create` (model, effort, caching, refusal fallback).
  Code that calls Claude takes a `client` argument so tests can pass a `FakeClient`.
- Every new event type is a `Payload` subclass in `backend/events/schema.py` and goes in `PAYLOAD_TYPES`.
  Events are stored before they are published (replay must be gap-free). Mirror it in `frontend/src/types.ts`
  and handle it in `applyEvents` (`frontend/src/useCityStream.ts`).
- The ingest pipeline must not import the DB or API layers; it only calls `emit()`.
- Never execute code from an ingested repo outside the Docker sandbox (ADR 0003, 0006). No secrets
  (`GITHUB_TOKEN`, `ANTHROPIC_API_KEY`) ever enter the sandbox or a model prompt.
- Outward-facing actions (pull requests) only on an explicit user request, for a human-confirmed finding.
- Only `https://github.com/<owner>/<repo>` URLs are accepted from the API; local paths are CLI/dev only.
- Schema changes: until Alembic lands, delete `codebase_city.db` after changing models (new tables alone are fine).
- `evals/benchmark/planted/` is deliberately buggy and excluded from ruff; don't "fix" it.
