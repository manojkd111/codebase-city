# 0009: Sonnet 5.5 reviews, Opus 5.5 writes fixes

**Status:** accepted (2026-11-09)

## Context
Every role ran on Claude Opus 5.5. A live analysis of pallets/itsdangerous (15 files) cost $0.87-0.94,
almost all of it output and cache writes from the three specialists. On the planted-bug benchmark:

| Run | Reviewers | Recall | Precision floor | Cost |
|---|---|---|---|---|
| 7 | Opus 5.5, effort medium | 11/13 | 79% | $0.42 |
| 8 | Opus 5.5, effort high | 13/13 | 84% | $0.42 |
| 10 | Sonnet 5.5, effort high | 13/13 | 79% | $0.18 |

Lowering effort saved ~7% and lost bugs; switching the model saved ~57% and kept recall.

## Decision
- **Two settings.** `review_model` (`CBC_REVIEW_MODEL`, default `claude-sonnet-5-5`) runs the
  coordinator and the specialists; `model` (`CBC_MODEL`, default `claude-opus-5-5`) runs the fix agent.
- **Why the split.** A wrong finding costs a click at triage. A bad fix becomes a pull request, so the
  fix agent keeps the strongest model.
- **Effort stays `high`** for specialists; it was not the lever.

## Consequences
- An analysis run records `review_model` as its model, and its cost is priced at that model.
- The benchmark is a small fixture (13 bugs, few runs per setting). Recheck recall on it, and precision
  on a live repo, whenever a prompt or model changes. Set `CBC_REVIEW_MODEL=claude-opus-5-5` to go back.
