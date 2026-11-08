"""Open a pull request for a stored fix. Only ever called on an explicit user request.

The token is used for the GitHub REST API and passed to git as a one-shot HTTP header
(`-c http.extraheader=...`), so it is never written to a remote URL, .git/config, logs, or
the sandbox. If the token can't push to the upstream repo, the change goes to a fork.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.ingest.source import _git

API = "https://api.github.com"
Http = Callable[[str, str, dict[str, Any] | None], tuple[int, Any]]


class GitHubError(RuntimeError):
    pass


def make_http(token: str) -> Http:
    def http(method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
        req = urllib.request.Request(
            API + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "codebase-city",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return r.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except json.JSONDecodeError:
                return e.code, {"message": raw.decode(errors="replace")[:500]}

    return http


@dataclass
class PRRequest:
    owner: str
    repo: str
    branch: str
    title: str
    body: str
    commit_message: str
    diff: str


def _ok(status: int, data: Any, what: str) -> Any:
    if status >= 300:
        msg = data.get("message") if isinstance(data, dict) else data
        raise GitHubError(f"GitHub {what} failed ({status}): {msg}")
    return data


def _auth_header(token: str) -> str:
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return f"http.extraheader=AUTHORIZATION: basic {basic}"


def default_push(token: str, workdir: Path, req: PRRequest, base_branch: str, target: str, author: str) -> None:
    """Clone the base branch, apply the diff (3-way), commit, push to `target`."""
    auth = ["-c", _auth_header(token)]
    dest = workdir / "pr"
    r = _git(
        [
            *auth,
            "clone",
            "--quiet",
            "--depth=50",
            "--branch",
            base_branch,
            f"https://github.com/{req.owner}/{req.repo}.git",
            str(dest),
        ]
    )
    if r.returncode:
        raise GitHubError(f"clone failed: {r.stderr.strip()[:300]}")
    patch = workdir / "fix.patch"
    patch.write_text(req.diff, encoding="utf-8")
    if _git(["checkout", "--quiet", "-b", req.branch], cwd=dest).returncode:
        raise GitHubError("could not create branch")
    r = _git(["apply", "--3way", "--whitespace=nowarn", str(patch)], cwd=dest)
    if r.returncode:
        raise GitHubError(
            "The fix no longer applies to the default branch (the code changed since the city was built). "
            "Rebuild the city and generate the fix again. " + r.stderr.strip()[:300]
        )
    _git(["add", "-A"], cwd=dest)
    name, email = author.split("|", 1)
    r = _git(
        ["-c", f"user.name={name}", "-c", f"user.email={email}", "commit", "--quiet", "-m", req.commit_message],
        cwd=dest,
    )
    if r.returncode:
        raise GitHubError(f"commit failed: {r.stderr.strip()[:300]}")
    r = _git([*auth, "push", "--quiet", f"https://github.com/{target}.git", f"HEAD:refs/heads/{req.branch}"], cwd=dest)
    if r.returncode:
        raise GitHubError(f"push failed: {r.stderr.strip()[:300]}")


def open_pull_request(
    token: str,
    req: PRRequest,
    workdir: Path,
    *,
    http: Http | None = None,
    push: Callable[..., None] = default_push,
    fork_wait_s: float = 30.0,
) -> str:
    http = http or make_http(token)
    me = _ok(*http("GET", "/user", None), "auth check")
    login = me["login"]
    upstream = _ok(*http("GET", f"/repos/{req.owner}/{req.repo}", None), "repo lookup")
    base = upstream["default_branch"]
    can_push = bool(upstream.get("permissions", {}).get("push"))

    if can_push:
        target, head = f"{req.owner}/{req.repo}", req.branch
    else:
        fork = _ok(*http("POST", f"/repos/{req.owner}/{req.repo}/forks", {"default_branch_only": True}), "fork")
        target, head = fork["full_name"], f"{login}:{req.branch}"
        deadline = time.monotonic() + fork_wait_s
        while http("GET", f"/repos/{target}", None)[0] != 200:  # forks are created asynchronously
            if time.monotonic() > deadline:
                raise GitHubError("Timed out waiting for the fork to be created.")
            time.sleep(2)

    author = f"{login}|{me.get('id', 0)}+{login}@users.noreply.github.com"
    push(token, workdir, req, base, target, author)
    pr = _ok(
        *http(
            "POST",
            f"/repos/{req.owner}/{req.repo}/pulls",
            # Draft: a maintainer has to mark it ready, so nothing an agent wrote can be merged by accident.
            {
                "title": req.title,
                "head": head,
                "base": base,
                "body": req.body,
                "maintainer_can_modify": True,
                "draft": True,
            },
        ),
        "pull request",
    )
    return pr["html_url"]
