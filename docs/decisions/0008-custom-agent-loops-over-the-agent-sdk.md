# 0008: Keep custom agent loops; the Agent SDK is an optional experiment

**Status:** accepted (2026-11-08)

## Context
The specialists and the fix agent run hand-written tool loops on the Messages API, all through
`llm.create`. The Claude Agent SDK offers a ready-made loop with built-in tools, subagents, hooks and
MCP support, and it was suggested as a replacement, starting with the fix agent.

## Decision
- **Keep the custom loops.** The SDK's built-in Bash and Edit tools run wherever the SDK process
  runs. On the host, that executes ingested code outside the sandbox (ADR 0003, 0006); inside the
  container, `ANTHROPIC_API_KEY` would have to enter the sandbox. Using the SDK safely means turning
  off its built-in tools and supplying our own, which is what the fix agent already does.
- **The loop does work the SDK would only wrap.** Every tool call is also an event (`agent.moved`
  drives the drones), every `submit_finding` is verified against the code before storage (ADR 0005),
  and the path-safe `Workspace` is a security boundary that the built-in Read tool is not.
- **Tests stay offline.** The suite fakes the client in `tests/fakes.py`; the SDK drives a separate
  runtime that would need a new fake layer.
- **Measure first.** The planted-bug benchmark is the yardstick for any change to the agent layer.
  Live baseline (five runs, Opus 5.5): before tuning, runs 1–3 averaged 92% recall and 77%
  precision (including known-real unplanted problems) at about $0.86 per run; after the coordinator
  and prompt changes, runs 4–5 reached 100% recall and 85% precision at about $0.67 per run.

## Consequences
- An SDK experiment is welcome if it is contained: one review specialist behind a flag, built-in
  tools disabled, only the existing MCP tools plus the verified `submit_finding`, `agent.moved`
  emitted from a hook, compared on `evals/benchmark`. The fix agent stays on the custom loop.
- We keep owning the loop code (`pause_turn`, `max_tokens`, refusals, turn budgets) in
  `backend/agents/specialist.py` and `backend/fixer/agent.py`.
