# 0005: Agents judge; code verifies every finding

**Status:** accepted (Week 3)

## Context
Review agents that hallucinate findings are worse than none: a made-up SQL injection costs a human
more time than it saves. Costs also have to stay predictable on large repos.

## Decision
- **Coordinator sees metrics only.** One structured-output call (`output_config.format`, low effort)
  gets the top 40 non-test files by risk, with import counts, and assigns up to `CBC_AGENT_FILES`
  files to each of three specialists. Unknown paths are dropped; an empty or failed plan falls back to
  the top hotspots, so a run always proceeds.
- **Specialists use a manual tool loop** (not the SDK tool runner) because each tool call is also an
  event: `agent.moved` drives the drones. Tools are strict-schema: `read_file`, `search_code`,
  `get_file_metrics`, `get_imports`, `submit_finding`. The history is append-only (thinking blocks
  are passed back unchanged), and the loop handles `pause_turn`, `max_tokens`, `refusal` and a turn budget.
- **Every finding is verified before storage.** The file must have been ingested, the line range must
  exist, and `evidence` must appear (whitespace-insensitive, ±2 lines) at those lines. Failures go
  back to the model as tool errors so it can correct itself; they're counted as `rejected`. Duplicates
  are dropped. Critical or low-confidence (< 0.6) findings get `needs_human_review`.
- **Reads are sandboxed by construction.** The `Workspace` only serves ingested paths, rejects `..`,
  absolute paths and symlinks at every level, and caps output. Repo text is declared untrusted in
  every system prompt.
- Every request uses `claude-opus-5-5`, adaptive thinking, explicit effort, automatic prompt caching
  and server-side refusal fallback (`fallbacks: "default"`), all in `llm.create`.

## Consequences
- A finding in the database always points at real code. Wrong *judgements* are still possible; that's
  what human triage and the planted-bug benchmark (`evals/benchmark`) are for.
- Specialists run in parallel threads, so `RunRecorder` serialises seq assignment per repo and
  re-reads the last seq from the database (also safe across worker processes).
