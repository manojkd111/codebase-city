"""Git churn: how many commits touched each file in the last 30 and 90 days."""

from __future__ import annotations

import time
from collections import Counter
from pathlib import Path

from .source import _git

DAY_S = 86_400


def compute_churn(repo: Path, now: float | None = None) -> dict[str, tuple[int, int]]:
    """Return {path: (commits_30d, commits_90d)}; empty if `repo` has no git history."""
    now = time.time() if now is None else now
    r = _git(
        ["log", "--since=90.days.ago", "--no-renames", "--format=%x00%ct", "--name-only"],
        cwd=repo,
    )
    if r.returncode != 0:
        return {}
    c30: Counter[str] = Counter()
    c90: Counter[str] = Counter()
    ts = 0.0
    for line in r.stdout.splitlines():
        if line.startswith("\x00"):
            ts = float(line[1:] or 0)
            continue
        path = line.strip()
        if not path:
            continue
        c90[path] += 1
        if now - ts <= 30 * DAY_S:
            c30[path] += 1
    return {p: (c30[p], c90[p]) for p in c90}
