# Architecture

```
Browser (R3F city + Mission Control: drones, beacons, triage, diffs)
   │  WS /ws/repos/{id}   (typed EventEnvelopes, replayable by seq)
FastAPI ── Jobs ──► threads (default)  or  arq workers via Redis (REDIS_URL)    [ADR 0007]
   │          │
   │          ├─ ingest:   clone → walk → districts(+folders) → churn → per-file metrics → imports → roads
   │          ├─ analyze:  coordinator (metrics only) → security / tests / debt specialists  [ADR 0005]
   │          │            tools read a path-safe Workspace; submit_finding is verified against the code
   │          ├─ fix:      fix agent edits a throwaway copy → Docker sandbox tests → diff   [ADR 0006]
   │          └─ open_pr:  apply diff to default branch → push (upstream or fork) → PR, on request only
   │
   │   every step: RunRecorder → INSERT events (seq) → publish (bus, or Redis → API relay)
   ▼
SQLite / Postgres: repos, districts, files, roads, import_edges, events, analysis_runs, findings, fixes
MCP servers (stdio): repo_metrics, findings, github — same DB, same gates
```

## Event stream
Each event is an `EventEnvelope {repo_id, seq, type, ts, data}`. Payload types live in
`backend/events/schema.py`. Order for one ingest:

1. `ingest.started`
2. `ingest.progress` (stage = cloned, walked)
3. `city.district_added` × N: sent before any building, with file counts, byte totals and per-folder
   file counts, so the frontend can lay out districts and folder blocks up front and buildings
   rise into fixed plots
4. `ingest.progress` (churn)
5. `city.building_added` × files, grouped by district (the city rises one district at a time)
6. `city.road_added` × roads
7. `ingest.completed` (after the DB snapshot is written) or `ingest.failed`

A WebSocket client subscribes first, then replays from the DB, then de-duplicates live events by
`seq`. Because events are stored before they are published, a late joiner never misses one.

## Hotspot risk
`risk = log1p(complexity_total) × (1 + log1p(churn_90)) × coverage_factor`, where
coverage_factor is 1.0 when coverage is unknown and ranges from 1.5 (0% covered) to 0.5 (100%).
Test files are flagged with `is_test` and excluded from hotspots by default.

## Agent and fix events
After the city: `analysis.started` → `agent.dispatched` × 3 → (`agent.turn`, `agent.tool_call`,
`agent.moved`, `finding.submitted`)* →
`agent.finished` × 3 → `analysis.completed` | `analysis.failed`. Triage emits `finding.updated`. A fix
emits `fix.started` → `fix.progress`* → `fix.completed` | `fix.failed`; a pull request emits
`fix.pr_opened` and `finding.updated(fixed)`. The frontend never polls for these: the drones, beacons
and Mission Control are all derived from the stream.
