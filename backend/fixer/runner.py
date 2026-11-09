"""Jobs for Week 6: generate a fix, open its pull request, collect coverage. Each one emits events."""

from __future__ import annotations

import logging
import shutil
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.agents import llm
from backend.agents.runner import repo_checkout
from backend.config import Settings
from backend.db.models import File, Finding, Fix, Repo
from backend.events.schema import FindingUpdated, FixCompleted, FixFailed, FixProgress, FixStarted, PullRequestOpened
from backend.ingest.pipeline import risk_score
from backend.ingest.runner import Publish, RunRecorder
from backend.ingest.source import _git, parse_github_url

from .agent import EditableWorkspace, git_files, run_fix_agent
from .github import GitHubError, PRRequest, open_pull_request
from .sandbox import DockerSandbox, SandboxUnavailable, detect_plan

log = logging.getLogger(__name__)
FINDING_FIELDS = (
    "category",
    "severity",
    "title",
    "file",
    "line_start",
    "line_end",
    "evidence",
    "explanation",
    "suggested_fix",
)


def _scratch_copy(src: Path, dest: Path, sha: str | None) -> Path:
    """A private, git-tracked copy of a local source, so edits never touch the original and
    `git diff` works even when the source isn't a git repository."""
    if (src / ".git").exists():
        _git(["clone", "--quiet", "--no-hardlinks", str(src), str(dest)])
        if sha:
            _git(["checkout", "--quiet", "--detach", sha], cwd=dest)
        return dest
    shutil.copytree(src, dest, symlinks=True, ignore=shutil.ignore_patterns(".git"))
    ident = ["-c", "user.name=codebase-city", "-c", "user.email=codebase-city@localhost"]
    _git(["init", "--quiet"], cwd=dest)
    _git(["add", "-A"], cwd=dest)
    _git([*ident, "commit", "--quiet", "--allow-empty", "-m", "baseline"], cwd=dest)
    return dest


SANDBOX_ARTEFACTS = (".cbc-venv", ".cbc-gomod", ".cbc-coverage.json", "node_modules")


def _diff(root: Path) -> tuple[str, int]:
    # Never ship sandbox artefacts, and never make git walk them: a venv built in the container
    # holds Linux symlinks that Windows can't read, and `git add -A` used to fail on them, which
    # read as "the agent made no changes" (live fix #2, itsdangerous).
    exclude = root / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with exclude.open("a", encoding="utf-8") as fh:
        fh.write("\n" + "\n".join(f"/{a}" for a in SANDBOX_ARTEFACTS) + "\n")
    added = _git(["add", "-A"], cwd=root)
    if added.returncode != 0:
        raise RuntimeError(f"git add failed while collecting the fix: {added.stderr.strip()[:500]}")
    _git(["reset", "-q", "--", *SANDBOX_ARTEFACTS], cwd=root)  # in case one was already tracked
    patch = _git(["diff", "--cached", "--binary"], cwd=root).stdout
    names = [n for n in _git(["diff", "--cached", "--name-only"], cwd=root).stdout.splitlines() if n]
    return patch, len(names)


def execute_fix(
    fix_id: int,
    settings: Settings,
    sessions: sessionmaker[Session],
    publish: Publish | None = None,
    *,
    client: Any = None,
    sandbox: DockerSandbox | None = None,
    allow_local: bool | None = None,
    workspace_root: Path | None = None,
) -> None:
    with sessions() as s:
        fix = s.get(Fix, fix_id)
        finding = s.get(Finding, fix.finding_id)
        repo = s.get(Repo, fix.repo_id)
        info = {k: getattr(finding, k) for k in FINDING_FIELDS}
        finding_id, repo_id, source, name, sha = (
            finding.id,
            repo.id,
            repo.source,
            repo.name or repo.source,
            repo.commit_sha,
        )
        fix.status = "running"
        s.commit()

    emit = RunRecorder(repo_id, sessions, publish)
    emit(FixStarted(fix_id=fix_id, finding_id=finding_id))

    def progress(step: str, detail: str) -> None:
        emit(FixProgress(fix_id=fix_id, finding_id=finding_id, step=step, detail=detail[:300]))

    outcome = None
    try:
        client = client or llm.make_client()
        if sandbox is None and settings.sandbox_enabled:
            sandbox = DockerSandbox(timeout_s=settings.sandbox_timeout_s)
        allow = settings.allow_local_sources if allow_local is None else allow_local
        with ExitStack() as stack:
            if workspace_root:
                root = Path(workspace_root)
            else:
                root = Path(
                    stack.enter_context(repo_checkout(source, sha, allow_local=allow, workdir=settings.workdir))
                )
                if not parse_github_url(source):
                    # A local source is the user's own working tree: edit a copy, never the original.
                    tmp = Path(
                        stack.enter_context(
                            tempfile.TemporaryDirectory(
                                prefix="cbc-fix-", dir=settings.workdir, ignore_cleanup_errors=True
                            )
                        )
                    )
                    root = _scratch_copy(root, tmp / "repo", sha)
            files = git_files(root)
            workspace = EditableWorkspace(root, files)
            plan = detect_plan(root, settings.sandbox_image)
            outcome = run_fix_agent(
                client,
                model=settings.model,
                effort=settings.effort,
                max_turns=settings.fixer_max_turns,
                finding=info,
                repo_name=name,
                workspace=workspace,
                sandbox=sandbox,
                plan=plan,
                progress=progress,
            )
            if outcome.error:
                raise RuntimeError(outcome.error)
            patch, changed = _diff(root)
            if not changed:
                raise RuntimeError(outcome.summary or "The agent made no changes.")

            # Final verdict comes from a fresh deterministic test run, not from the agent's word.
            tests_passed, test_output = None, outcome.sandbox_note or ""
            if plan and sandbox is not None and sandbox.available():
                progress("test", "final test run")
                try:
                    final = sandbox.run_tests(root, plan, setup=outcome.last_test is None)
                    tests_passed, test_output = final.passed, final.output
                except SandboxUnavailable as exc:
                    test_output = str(exc)

        with sessions() as s:
            fix = s.get(Fix, fix_id)
            fix.status = "ready"
            fix.summary = outcome.summary or info["title"]
            fix.diff = patch
            fix.files_changed = changed
            fix.tests_passed = tests_passed
            fix.test_output = test_output[-20000:]
            fix.commit_message = outcome.commit_message or f"Fix: {info['title']}"[:72]
            fix.input_tokens = outcome.usage.input_tokens
            fix.output_tokens = outcome.usage.output_tokens
            fix.error = None
            s.commit()
        emit(FixCompleted(fix_id=fix_id, finding_id=finding_id, files_changed=changed, tests_passed=tests_passed))
    except Exception as exc:
        log.exception("fix %s failed", fix_id)
        message = str(exc)[:1000] or type(exc).__name__
        with sessions() as s:
            fix = s.get(Fix, fix_id)
            fix.status, fix.error = "failed", message
            if outcome is not None:  # a failure after the agent ran still cost tokens
                fix.input_tokens = outcome.usage.input_tokens
                fix.output_tokens = outcome.usage.output_tokens
            s.commit()
        emit(FixFailed(fix_id=fix_id, finding_id=finding_id, error=message))


def pr_body(finding: Finding, fix: Fix) -> str:
    tests = {True: "passed", False: "**failed**", None: "not run"}[fix.tests_passed]
    return (
        f"{fix.summary}\n\n"
        f"### Problem\n**{finding.title}** ({finding.category}, {finding.severity})  \n"
        f"`{finding.file}` lines {finding.line_start}-{finding.line_end}\n\n"
        f"{finding.explanation}\n\n"
        f"### Verification\nTests in an isolated sandbox: {tests}. A human confirmed the finding before this PR.\n\n"
        "---\nOpened with [Codebase City](https://github.com/) by a Claude agent; please review carefully."
    )


def execute_open_pr(
    fix_id: int,
    settings: Settings,
    sessions: sessionmaker[Session],
    publish: Publish | None = None,
    **pr_kwargs: Any,
) -> str | None:
    with sessions() as s:
        fix = s.get(Fix, fix_id)
        finding = s.get(Finding, fix.finding_id)
        repo = s.get(Repo, fix.repo_id)
        repo_id, finding_id, source = repo.id, finding.id, repo.source
        req_body, commit_msg, diff = pr_body(finding, fix), fix.commit_message or f"Fix #{finding.id}", fix.diff
        title = (fix.summary.splitlines()[0] if fix.summary else finding.title)[:100]
    emit = RunRecorder(repo_id, sessions, publish)
    try:
        if not settings.github_token:
            raise GitHubError("GITHUB_TOKEN is not set on the server, so a pull request can't be opened.")
        gh = parse_github_url(source)
        if not gh:
            raise GitHubError("Pull requests can only be opened for GitHub repositories.")
        req = PRRequest(gh[0], gh[1], f"codebase-city/fix-{fix_id}", title, req_body, commit_msg, diff)
        with tempfile.TemporaryDirectory(prefix="cbc-pr-", dir=settings.workdir, ignore_cleanup_errors=True) as tmp:
            url = open_pull_request(settings.github_token, req, Path(tmp), **pr_kwargs)
        with sessions() as s:
            fix = s.get(Fix, fix_id)
            fix.status, fix.pr_url, fix.error = "pr_opened", url, None
            s.get(Finding, finding_id).status = "fixed"
            s.commit()
        emit(PullRequestOpened(fix_id=fix_id, finding_id=finding_id, url=url))
        emit(FindingUpdated(finding_id=finding_id, status="fixed"))
        return url
    except Exception as exc:
        log.exception("PR for fix %s failed", fix_id)
        message = str(exc)[:1000] or type(exc).__name__
        with sessions() as s:
            s.get(Fix, fix_id).error = message
            s.commit()
        emit(FixFailed(fix_id=fix_id, finding_id=finding_id, error=message))
        return None


def execute_coverage(
    repo_id: int,
    settings: Settings,
    sessions: sessionmaker[Session],
    *,
    sandbox: DockerSandbox | None = None,
    allow_local: bool | None = None,
) -> dict[str, float]:
    """Run the repo's tests with coverage in the sandbox; store per-file coverage and update risk."""
    if sandbox is None:
        if not settings.sandbox_enabled:
            raise SandboxUnavailable("The test sandbox is turned off (CBC_SANDBOX=off).")
        sandbox = DockerSandbox(timeout_s=settings.sandbox_timeout_s)
    with sessions() as s:
        repo = s.get(Repo, repo_id)
        source, sha = repo.source, repo.commit_sha
    allow = settings.allow_local_sources if allow_local is None else allow_local
    with repo_checkout(source, sha, allow_local=allow, workdir=settings.workdir) as root:
        plan = detect_plan(Path(root), settings.sandbox_image)
        if plan is None:
            return {}
        cov = sandbox.coverage(Path(root), plan)
    if cov:
        with sessions() as s:
            for f in s.scalars(select(File).where(File.repo_id == repo_id)):
                if f.path in cov:
                    f.coverage = cov[f.path]
                    f.risk = risk_score(f.complexity_total, f.churn_90, f.coverage)
            s.commit()
    return cov
