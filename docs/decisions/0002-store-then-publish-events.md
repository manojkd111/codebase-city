# 0002: Store every event before publishing it

**Status:** accepted (Week 1)

## Context
The demo depends on live events (the city rising, drones flying), but browsers reconnect, people
join halfway through, and the plan requires "replay mode" for a demo that fails on stage.

## Decision
Every payload gets a per-repo sequence number, is written to the `events` table, and only then
published on the in-process bus. A WebSocket subscribes first, replays from the DB, then streams
live events and drops any with `seq <= last_sent`.

## Consequences
- Replays are exact and gap-free, and replay mode costs nothing extra.
- One commit per event. SQLite runs in WAL mode with `synchronous=NORMAL` to keep this cheap
  (91 files ingest in about 6s, end to end).
- The bus is in-process. Once agents run in separate workers (Week 4) it gets swapped for Redis
  pub/sub behind the same three-method interface.
