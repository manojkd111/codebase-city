# 0007: Optional worker processes via Redis

**Status:** accepted (Week 4)

## Context
Agent runs and sandboxed test suites take minutes. In-process threads are fine for one developer but
tie long work to the API process.

## Decision
- The API talks to a four-method `Jobs` interface (`backend/jobs.py`). Without `REDIS_URL`, jobs run
  in threads in the API process (`InProcessJobs`). With it, they're enqueued to arq (`QueuedJobs`) and
  run by `python -m arq backend.worker.WorkerSettings`; the job functions are the same in both modes.
- Workers publish events to the Redis channel `cbc:events`; the API relays them into its in-process bus
  for WebSockets. Events are still stored before publishing (ADR 0002), so Redis only carries the live
  tail. A dropped message is recovered by replay on reconnect.

## Consequences
- Local dev needs nothing extra; the deployed shape is one config change away.
- The DB is the single source of truth for `seq`; `RunRecorder` re-reads it and retries on a unique-key
  conflict, so several processes can append to the same repo's stream.
