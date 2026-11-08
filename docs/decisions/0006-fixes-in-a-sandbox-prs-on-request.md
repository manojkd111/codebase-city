# 0006: Fixes are tested in a sandbox; pull requests only on request

**Status:** accepted (Week 6)

## Context
Generating a patch is cheap and private. Running a repo's tests means executing untrusted code, and
opening a pull request is visible to strangers.

## Decision
- **Gates.** A fix can only be generated for a finding a human confirmed. A PR can only be opened
  for a `ready` fix of a still-confirmed finding, on an explicit `POST /api/fixes/{id}/pr` (the UI
  asks for confirmation first). The MCP tool enforces the same rules and is annotated open-world.
- **Edits happen in a throwaway copy.** Remote repos are cloned to a temp directory at the ingested
  commit; local sources are copied (with a baseline commit if they aren't git repos). The original is
  never written. Only the diff is stored. `edit_file` needs a unique exact match and preserves CRLF.
- **Sandbox (Docker).** Two phases on the same throwaway mount: setup installs dependencies with
  network but no secrets (`npm --ignore-scripts`); tests run with `--network none`. Both use
  `--cap-drop ALL`, `no-new-privileges`, and pid/memory/CPU limits. No host environment is passed, and
  even the docker CLI gets an environment scrubbed of anything credential-like. Without Docker (or
  with `CBC_SANDBOX=off`) the fixer still works, and the fix is labelled "tests not run".
- **The verdict comes from code.** After the agent finishes, a fresh test run decides `tests_passed`;
  the agent's own claim is not trusted.
- **Pull requests.** The diff is applied with `git apply --3way` to the default branch's head. The
  push goes upstream if the token can push, otherwise to a fork. The token travels only as a one-shot
  `http.extraheader` and REST header: never in a URL, `.git/config`, a prompt or the sandbox.

## Consequences
- Setup scripts (`pip install -e .`) can still run repo code with network access, inside the container
  and without secrets. Tightening this means pre-built images or a dependency proxy.
- A fix generated against an old commit may no longer apply; the PR step says so and asks for a rebuild.
