# 0003: Never execute ingested code on the host

**Status:** accepted (Week 1)

## Context
The plan says to run a repo's tests with coverage during ingestion. But a test suite is arbitrary
code from an arbitrary repo, and running it on the API host means remote code execution.

## Decision
Ingestion only *reads* files. `coverage` is `null` ("unknown") for now. Coverage will be collected
in the same disposable Docker sandbox as the fix worker (Week 6): no network, no secrets.

Related guards that are already in place:
- The API accepts only `https://github.com/<owner>/<repo>` URLs (no file://, ssh, or other hosts).
- Symlinks are never followed during the walk, so a clone can't point the walker at host files.
- `GIT_TERMINAL_PROMPT=0`, so private or missing repos fail fast instead of hanging.
- Files over 512 KB and generated files (`.min.js`, `.d.ts`, `_pb2.py`) are skipped.

## Consequences
Building color can't use coverage until Week 6. Until then, health comes from agent findings.
