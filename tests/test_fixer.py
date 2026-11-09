from __future__ import annotations

import json
from dataclasses import replace

import pytest
from sqlalchemy import select

from backend.agents.workspace import WorkspaceError
from backend.db.models import EventRow, Finding, Fix
from backend.fixer.agent import EditableWorkspace
from backend.fixer.github import GitHubError, PRRequest, open_pull_request
from backend.fixer.runner import execute_fix, execute_open_pr
from backend.fixer.sandbox import DockerSandbox, SandboxResult, detect_plan, parse_coverage_json, scrubbed_env

from .conftest import FIXTURE_FILES
from .fakes import FakeClient, response, text, tool
from .test_agents import GOOD, _setup

# ---- editable workspace ----------------------------------------------------------------------


def test_edit_requires_a_unique_exact_match(fixture_repo):
    ws = EditableWorkspace(fixture_repo, ["pkg/core.py", "pkg/util.py"])
    with pytest.raises(WorkspaceError, match="matched 0 times"):
        ws.edit("pkg/core.py", "nope", "x")
    with pytest.raises(WorkspaceError, match="matched 3 times"):
        ws.edit("pkg/core.py", "return", "yield")
    assert "line 2" in ws.edit("pkg/util.py", "return x * 2", "return x + x")
    assert "x + x" in ws.read("pkg/util.py")  # cache invalidated


def test_edit_matches_crlf_files(tmp_path):
    (tmp_path / "a.py").write_bytes(b"def f():\r\n    return 1\r\n")
    ws = EditableWorkspace(tmp_path, ["a.py"])
    ws.edit("a.py", "def f():\n    return 1", "def f():\n    return 2")
    assert (tmp_path / "a.py").read_bytes() == b"def f():\r\n    return 2\r\n"


def test_create_refuses_existing_escaping_and_git_paths(fixture_repo):
    ws = EditableWorkspace(fixture_repo, ["pkg/core.py"])
    for bad in ("pkg/core.py", "../outside.py", ".git/hooks/pre-commit", "pkg/../../x.py"):
        with pytest.raises(WorkspaceError):
            ws.create(bad, "x")
    assert ws.create("tests/test_new.py", "def test_x():\n    assert True\n") == "Created tests/test_new.py."
    assert "assert True" in ws.read("tests/test_new.py")


# ---- sandbox (no Docker needed) ----------------------------------------------------------------


def test_detect_plan(fixture_repo, tmp_path):
    plan = detect_plan(fixture_repo)
    assert plan and plan.image.startswith("python") and "pytest" in plan.test
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))
    assert detect_plan(tmp_path).test == "npm test --silent"
    assert detect_plan(tmp_path / "missing") is None


def test_test_phase_has_no_network_and_no_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    sb = DockerSandbox()
    test_args = sb._args(tmp_path, "python:3.12-slim", False, "pytest")
    assert test_args[test_args.index("--network") + 1] == "none"
    assert "--cap-drop" in test_args and "no-new-privileges" in test_args
    assert not any("secret" in a for a in test_args)
    env = scrubbed_env()
    assert "GITHUB_TOKEN" not in env and "ANTHROPIC_API_KEY" not in env and "PATH" in {k.upper() for k in env}


def test_parse_coverage_json(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"files": {"/work/pkg/core.py": {"summary": {"num_statements": 10, "covered_lines": 7}}}}))
    assert parse_coverage_json(p) == {"pkg/core.py": 0.7}
    assert parse_coverage_json(tmp_path / "missing.json") == {}


class FakeSandbox:
    def __init__(self, passed=True, available=True):
        self.passed, self._available, self.runs = passed, available, 0

    def available(self):
        return self._available

    def run_tests(self, root, plan, *, setup=True):
        self.runs += 1
        return SandboxResult(0 if self.passed else 1, "1 passed" if self.passed else "1 failed")


# ---- fix job -------------------------------------------------------------------------------------


def _make_fix(sessions, repo_id):
    with sessions() as s:
        f = Finding(repo_id=repo_id, **{k: v for k, v in GOOD.items()}, needs_human_review=False, status="confirmed")
        s.add(f)
        s.commit()
        fix = Fix(finding_id=f.id, repo_id=repo_id)
        s.add(fix)
        s.commit()
        return f.id, fix.id


def test_fix_edits_a_copy_tests_in_sandbox_and_stores_the_diff(tmp_path, fixture_repo):
    settings, sessions, repo_id, _ = _setup(tmp_path, fixture_repo)
    finding_id, fix_id = _make_fix(sessions, repo_id)
    client = FakeClient(
        {
            "fix": [
                response(tool("read_file", {"path": "pkg/core.py", "start_line": 1, "end_line": 0})),
                response(
                    tool(
                        "edit_file",
                        {"path": "pkg/core.py", "old_text": "    return os.sep\n", "new_text": "    return -1\n"},
                    )
                ),
                response(tool("run_tests", {})),
                response(
                    tool(
                        "finish",
                        {
                            "summary": "Return -1 instead of a path separator.",
                            "commit_message": "Return a consistent int from run()\nmore",
                        },
                    )
                ),
            ]
        },
        lambda kw: "fix",
    )
    sandbox = FakeSandbox(passed=True)
    execute_fix(fix_id, settings, sessions, client=client, sandbox=sandbox, allow_local=True)

    with sessions() as s:
        fix = s.get(Fix, fix_id)
        types = [e.type for e in s.scalars(select(EventRow).where(EventRow.repo_id == repo_id))]
    assert fix.status == "ready", fix.error
    assert "-    return os.sep" in fix.diff and "+    return -1" in fix.diff and fix.files_changed == 1
    assert fix.tests_passed is True and sandbox.runs == 2  # the agent's run + the final verdict
    assert fix.commit_message == "Return a consistent int from run()"
    assert "fix.started" in types and "fix.progress" in types and types[-1] == "fix.completed"
    # The user's working tree was never touched.
    assert (fixture_repo / "pkg/core.py").read_text() == FIXTURE_FILES["pkg/core.py"] + "\n# touched\n"
    assert "run_tests" in [t["name"] for t in client.calls[0]["tools"]]


def test_fix_without_docker_hides_run_tests_and_reports_untested(tmp_path, fixture_repo):
    settings, sessions, repo_id, _ = _setup(tmp_path, fixture_repo)
    _, fix_id = _make_fix(sessions, repo_id)
    client = FakeClient(
        {
            "fix": [
                response(tool("edit_file", {"path": "pkg/util.py", "old_text": "x * 2", "new_text": "2 * x"})),
                response(tool("finish", {"summary": "s", "commit_message": "c"})),
            ]
        },
        lambda kw: "fix",
    )
    execute_fix(fix_id, settings, sessions, client=client, sandbox=FakeSandbox(available=False), allow_local=True)
    with sessions() as s:
        fix = s.get(Fix, fix_id)
    assert fix.status == "ready" and fix.tests_passed is None and "Docker is not available" in fix.test_output
    assert "run_tests" not in [t["name"] for t in client.calls[0]["tools"]]


def test_fix_with_no_changes_fails(tmp_path, fixture_repo):
    settings, sessions, repo_id, _ = _setup(tmp_path, fixture_repo)
    _, fix_id = _make_fix(sessions, repo_id)
    client = FakeClient({"fix": [response(text("This is not actually a bug."))]}, lambda kw: "fix")
    execute_fix(fix_id, settings, sessions, client=client, sandbox=FakeSandbox(), allow_local=True)
    with sessions() as s:
        fix = s.get(Fix, fix_id)
    assert fix.status == "failed" and "not actually a bug" in fix.error


# ---- pull requests -------------------------------------------------------------------------------


class FakeGitHub:
    def __init__(self, can_push: bool):
        self.can_push, self.calls = can_push, []

    def __call__(self, method, path, body):
        self.calls.append((method, path, body))
        if path == "/user":
            return 200, {"login": "manoj", "id": 7}
        if path == "/repos/pallets/click":
            return 200, {"default_branch": "main", "permissions": {"push": self.can_push}}
        if path == "/repos/pallets/click/forks":
            return 202, {"full_name": "manoj/click"}
        if path == "/repos/manoj/click":
            return 200, {}
        if path == "/repos/pallets/click/pulls":
            return 201, {"html_url": "https://github.com/pallets/click/pull/1"}
        return 404, {"message": "not found"}


REQ = PRRequest("pallets", "click", "codebase-city/fix-1", "t", "b", "c", "diff")


@pytest.mark.parametrize(
    "can_push,target,head",
    [(True, "pallets/click", "codebase-city/fix-1"), (False, "manoj/click", "manoj:codebase-city/fix-1")],
)
def test_open_pull_request_pushes_or_forks(tmp_path, can_push, target, head):
    gh, pushed = FakeGitHub(can_push), []
    url = open_pull_request("tok", REQ, tmp_path, http=gh, push=lambda *a: pushed.append(a), fork_wait_s=0)
    assert url == "https://github.com/pallets/click/pull/1"
    assert pushed[0][3] == "main" and pushed[0][4] == target
    pr = [b for m, p, b in gh.calls if p.endswith("/pulls")][0]
    assert pr["head"] == head and pr["base"] == "main" and pr["draft"] is True
    assert not any("tok" in json.dumps(b or {}) for _, _, b in gh.calls)  # token only ever in headers


def test_open_pull_request_surfaces_github_errors(tmp_path):
    def denied(method, path, body):
        return 401, {"message": "Bad credentials"}

    with pytest.raises(GitHubError, match="Bad credentials"):
        open_pull_request("tok", REQ, tmp_path, http=denied, push=lambda *a: None)


def test_pr_requires_a_token_and_a_github_repo(tmp_path, fixture_repo):
    settings, sessions, repo_id, _ = _setup(tmp_path, fixture_repo)
    _, fix_id = _make_fix(sessions, repo_id)
    assert execute_open_pr(fix_id, replace(settings, github_token=None), sessions) is None
    with sessions() as s:
        assert "GITHUB_TOKEN" in s.get(Fix, fix_id).error
    assert execute_open_pr(fix_id, replace(settings, github_token="t"), sessions) is None
    with sessions() as s:
        assert "only be opened for GitHub" in s.get(Fix, fix_id).error


def test_fix_works_for_a_plain_folder_source(tmp_path):
    """A local source without .git: the fixer copies it, commits a baseline, and diffs against that."""
    from backend.fixer.runner import _diff, _scratch_copy

    src = tmp_path / "plain"
    (src / "pkg").mkdir(parents=True)
    (src / "pkg" / "a.py").write_text("x = 1\n")
    copy = _scratch_copy(src, tmp_path / "copy", None)
    (copy / "pkg" / "a.py").write_text("x = 2\n")
    patch, changed = _diff(copy)
    assert changed == 1 and "+x = 2" in patch
    assert (src / "pkg" / "a.py").read_text() == "x = 1\n" and not (src / ".git").exists()


def test_diff_never_walks_or_ships_sandbox_artefacts(tmp_path):
    # Live fix #2: git choked on the container venv's Linux symlinks and the fix read as "no changes".
    from backend.fixer.runner import _diff, _scratch_copy

    src = tmp_path / "plain"
    src.mkdir()
    (src / "a.py").write_text("x = 1\n")
    copy = _scratch_copy(src, tmp_path / "copy", None)
    (copy / "a.py").write_text("x = 2\n")
    (copy / ".cbc-venv" / "lib").mkdir(parents=True)
    (copy / ".cbc-venv" / "lib" / "site.py").write_text("junk\n")
    (copy / ".cbc-coverage.json").write_text("{}")
    patch, changed = _diff(copy)
    assert changed == 1 and ".cbc-" not in patch
    assert "/.cbc-venv" in (copy / ".git" / "info" / "exclude").read_text()


def test_detect_plan_installs_the_pyproject_test_dependency_group(tmp_path):
    # itsdangerous keeps freezegun in [dependency-groups] tests; without it the suite can't import.
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\n[dependency-groups]\ndocs = ["sphinx"]\ntests = ["freezegun", "pytest"]\n'
        '"evil; rm -rf /" = ["x"]\n'
    )
    setup = detect_plan(tmp_path).setup
    assert "pip install -q --group tests" in setup and "evil" not in setup and "docs" not in setup
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    assert "--group" not in detect_plan(tmp_path).setup
