# 0004: The city is laid out in the browser, from the event stream

**Status:** accepted (Week 2)

## Context
The backend emits districts, buildings and roads as events. Something has to turn them into
positions, and the city should rise live during ingest and look identical on replay.

## Decision
- The frontend (`frontend/`, Vite + React Three Fiber) builds the city **only** from the
  WebSocket stream. A finished city is a full replay, so live and finished cities share one code
  path; `/api/repos/{id}/city` stays a debugging snapshot.
- Layout is deterministic and lives in `frontend/src/layout.ts`: a squarified treemap of
  districts (area blends file count and bytes); inside each district, a treemap of folder blocks
  (one per directory, sized by file count, separated by alleys); inside each block, a fixed grid of
  plots. `city.district_added` carries `folders` (dirname -> file count) so all of this is fixed
  before any building arrives, and a building never moves once it appears. Slots follow arrival
  order within a folder, which the backend keeps stable. Events stored before `folders` existed
  fall back to folders seen so far, so those cities may shuffle while loading; re-ingest to fix.
- Mapping: height = `complexity_total` (sqrt-scaled), footprint = file size relative to its
  district's average, colour = `risk` (blue → amber → red; tests greyed), arcs = district roads.
- Buildings are one `InstancedMesh`, so thousands of files are still a single draw call.

## Consequences
- No layout data in the DB or the event contract; changing the look never needs a migration.
- The backend doesn't know positions. If agents need to point at a building (Week 4 drones),
  they address it by path and the frontend resolves the position.
- Colour uses `risk` until coverage and findings exist (Week 3+). Churn windows are not drawn yet.
