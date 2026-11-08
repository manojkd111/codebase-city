"""Resolve an ingest source (GitHub URL or local path) to a directory on disk."""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

GITHUB_URL = re.compile(r"^https://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$")
CHURN_WINDOW_DAYS = 90
GIT_TIMEOUT_S = 300


class SourceError(ValueError):
    """The source cannot be ingested (bad URL, clone failure, missing path)."""


@dataclass(frozen=True)
class ResolvedSource:
    path: Path
    name: str
    commit_sha: str | None
    is_git: bool


def parse_github_url(url: str) -> tuple[str, str] | None:
    m = GITHUB_URL.match(url.strip())
    return (m["owner"], m["repo"]) if m else None


def _git(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}  # never hang on a credential prompt
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=GIT_TIMEOUT_S,
    )


def head_sha(path: Path) -> str | None:
    r = _git(["rev-parse", "HEAD"], cwd=path)
    return r.stdout.strip() if r.returncode == 0 else None


def _rmtree(path: Path) -> None:
    # Git marks pack files read-only; on Windows rmtree needs them made writable first.
    def onexc(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)

    shutil.rmtree(path, onexc=onexc)


def _clone(url: str, dest: Path) -> None:
    since = (datetime.now(UTC) - timedelta(days=CHURN_WINDOW_DAYS + 1)).strftime("%Y-%m-%d")
    base = ["clone", "--quiet", "--single-branch", "--no-tags", "--filter=blob:none"]
    # Shallow history back to the churn window: enough for churn, far cheaper than a full clone.
    r = _git([*base, f"--shallow-since={since}", url, str(dest)])
    if r.returncode != 0:
        # No commits in the window makes --shallow-since fail; fall back to a depth-1 clone.
        if dest.exists():
            _rmtree(dest)
        r = _git([*base, "--depth=1", url, str(dest)])
    if r.returncode != 0:
        raise SourceError(f"git clone failed: {r.stderr.strip()[:500]}")


@contextmanager
def resolve_source(source: str, *, allow_local: bool, workdir: str | None = None) -> Iterator[ResolvedSource]:
    """Yield a directory for `source`; temporary clones are deleted afterwards."""
    gh = parse_github_url(source)
    if gh:
        owner, repo = gh
        tmp = Path(tempfile.mkdtemp(prefix="cbc-", dir=workdir))
        dest = tmp / repo
        try:
            _clone(f"https://github.com/{owner}/{repo}.git", dest)
            yield ResolvedSource(dest, f"{owner}/{repo}", head_sha(dest), is_git=True)
        finally:
            _rmtree(tmp)
        return

    if not allow_local:
        raise SourceError("Only public https://github.com/<owner>/<repo> URLs are accepted.")
    path = Path(source).expanduser().resolve()
    if not path.is_dir():
        raise SourceError(f"Not a directory: {path}")
    sha = head_sha(path)
    yield ResolvedSource(path, path.name, sha, is_git=sha is not None)


def checkout(path: Path, sha: str) -> bool:
    """Best effort: move a clone to `sha` (the commit that was ingested). False if unreachable."""
    if head_sha(path) == sha:
        return True
    if _git(["checkout", "--quiet", "--detach", sha], cwd=path).returncode == 0:
        return True
    # Outside the shallow window: fetch just that commit.
    if _git(["fetch", "--quiet", "--depth=1", "origin", sha], cwd=path).returncode != 0:
        return False
    return _git(["checkout", "--quiet", "--detach", sha], cwd=path).returncode == 0
