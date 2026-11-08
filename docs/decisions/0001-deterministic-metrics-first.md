# 0001: Deterministic metrics before any model call

**Status:** accepted (Week 1)

## Context
A 5,000-file repo is too large and too expensive to send to Claude whole.

## Decision
Ingestion is pure code: tree-sitter for imports and function counts, lizard for cyclomatic
complexity in 20+ languages, and `git log` for churn. These combine into a hotspot `risk` score.
The coordinator agent will only see these metrics, never the whole repo; specialists read code
only in the hotspots they are given.

## Consequences
- Claude reads perhaps 10–20% of files; cost per repo stays low and predictable.
- The metrics are reproducible and unit-testable, so the city looks the same on every run.
- The risk formula is a heuristic. It will be tuned against the planted-bug benchmark in Week 3.
