from __future__ import annotations

from dataclasses import replace

import pytest
from sqlalchemy import select

from backend.agents.coordinator import candidates, make_plan
from backend.agents.runner import execute_analysis
from backend.agents.tools import ToolBox
from backend.agents.workspace import Workspace, WorkspaceError, evidence_in_lines
from backend.config import get_settings
from backend.db.models import AnalysisRun, EventRow, Finding, Repo
from backend.db.session import make_engine, make_session_factory
from backend.ingest.runner import execute_ingest

from .fakes import FakeClient, agent_route, plan_json, response, text, tool

# ---- workspace -----------------------------------------------------------------------------


def test_workspace_reads_only_ingested_files(fixture_repo):
    ws = Workspace(fixture_repo, ["pkg/core.py", "pkg/util.py"])
    out = ws.read("pkg/core.py", 4, 6)
    assert "4| def run(x, y):" in out and "6|         return util.helper(x)" in out
    for bad in ("../secret.txt", "/etc/passwd", "pkg/../setup.py", "setup.py", "C:/Windows/win.ini"):
        with pytest.raises(WorkspaceError):
            ws.read(bad)
    with pytest.raises(WorkspaceError):
        ws.read("pkg/core.py", 50, 60)  # past the end
    assert "pkg/util.py:2:" in ws.search(r"x \* 2", "pkg/*")
    with pytest.raises(WorkspaceError):
        ws.search("(unclosed")


def test_evidence_check_is_whitespace_tolerant_but_exact():
    lines = ["def run(x, y):", "    if x and y:", "        return util.helper(x)"]
    assert evidence_in_lines("if x and y:\n        return util.helper(x)", lines, 2, 3)
    assert evidence_in_lines("if x and y: return util.helper(x)", lines, 2, 3)
    assert evidence_in_lines("def run(x, y): ... return util.helper(x)", lines, 1, 3)
    assert not evidence_in_lines("eval(user_input)", lines, 1, 3)  # made up
    assert not evidence_in_lines("x", lines, 1, 3)  # too short to mean anything


def _toolbox(fixture_repo, role="debt"):
    stored = []
    tb = ToolBox(
        role,
        Workspace(fixture_repo, ["pkg/core.py", "pkg/util.py"]),
        {"pkg/core.py": {"risk": 1.0}, "pkg/util.py": {"risk": 0.5}},
        {"pkg/core.py": ["pkg/util.py"]},
        {"pkg/util.py": ["pkg/core.py"]},
        lambda f: stored.append(f) or len(stored),
    )
    return tb, stored


GOOD = {
    "category": "debt",
    "severity": "medium",
    "file": "pkg/core.py",
    "line_start": 7,
    "line_end": 9,
    "title": "Loop returns only for the first matching i",
    "evidence": "for i in range(3):\n        if i == y:\n            return i",
    "explanation": "y outside 0..2 falls through to os.sep, a string, from a function that returns ints.",
    "suggested_fix": "Return a consistent type or raise.",
    "confidence": 0.8,
}


def test_submit_finding_is_verified_against_the_code(fixture_repo):
    tb, stored = _toolbox(fixture_repo)
    assert not tb.run("submit_finding", GOOD).is_error
    assert stored[0].file == "pkg/core.py" and not stored[0].needs_human_review

    bad_evidence = tb.run("submit_finding", {**GOOD, "title": "x", "evidence": "eval(request.args['q'])"})
    assert bad_evidence.is_error and "evidence was not found" in bad_evidence.content
    bad_lines = tb.run("submit_finding", {**GOOD, "line_start": 90, "line_end": 95})
    assert bad_lines.is_error and "not within the file" in bad_lines.content
    wrong_role = tb.run("submit_finding", {**GOOD, "category": "security"})
    assert wrong_role.is_error and "category must be 'debt'" in wrong_role.content
    unknown = tb.run("submit_finding", {**GOOD, "file": "pkg/missing.py"})
    assert unknown.is_error
    dup = tb.run("submit_finding", GOOD)
    assert dup.is_error and "Duplicate" in dup.content
    assert len(stored) == 1 and tb.rejected == 4

    other_lines = {"line_start": 4, "line_end": 5, "evidence": "def run(x, y):\n    if x and y:"}
    low = tb.run("submit_finding", {**GOOD, **other_lines, "title": "other", "confidence": 0.3})
    assert not low.is_error and stored[-1].needs_human_review


def test_registry_dedupes_the_same_problem_across_agents(fixture_repo):
    from backend.agents.tools import FindingRegistry

    registry = FindingRegistry()
    boxes = {}
    for role in ("security", "debt", "tests"):
        tb, stored = _toolbox(fixture_repo, role)
        tb.registry = registry
        boxes[role] = (tb, stored)

    debt, _ = boxes["debt"]
    assert not debt.run("submit_finding", GOOD).is_error
    sec, sec_stored = boxes["security"]
    dup = sec.run("submit_finding", {**GOOD, "category": "security", "title": "same bug, other angle"})
    assert dup.is_error and "finding #1 from the debt reviewer covers overlapping lines" in dup.content
    assert sec_stored == [] and sec.duplicates == 1 and sec.rejected == 0
    # A deliberate resubmission means "this is a different problem": the agent decides, so it's stored.
    again = sec.run("submit_finding", {**GOOD, "category": "security", "title": "same bug, other angle"})
    assert not again.is_error and len(sec_stored) == 1
    tests, tests_stored = boxes["tests"]  # "this is untested" is a different problem on the same lines
    assert not tests.run("submit_finding", {**GOOD, "category": "tests", "title": "no test"}).is_error
    far = {"line_start": 4, "line_end": 4, "evidence": "def run(x, y):"}  # line 4 vs 7-9: not overlapping
    assert not sec.run("submit_finding", {**GOOD, **far, "category": "security", "title": "x"}).is_error


def test_tool_errors_are_returned_not_raised(fixture_repo):
    tb, _ = _toolbox(fixture_repo)
    assert tb.run("read_file", {"path": "../../etc/passwd", "start_line": 1, "end_line": 5}).is_error
    assert tb.run("nope", {}).is_error
    assert tb.run("read_file", {"wrong": 1}).is_error
    r = tb.run("get_imports", {"path": "pkg/core.py"})
    assert '"imports": ["pkg/util.py"]' in r.content and r.moved_to == "pkg/core.py"


# ---- coordinator ---------------------------------------------------------------------------


FILES = [
    {
        "path": p,
        "loc": 10,
        "functions": 1,
        "complexity_total": c,
        "complexity_max": c,
        "churn_90": 0,
        "risk": r,
        "is_test": t,
    }
    for p, c, r, t in [
        ("a.py", 5, 3.0, False),
        ("b.py", 3, 2.0, False),
        ("c.py", 1, 1.0, False),
        ("test_a.py", 9, 9.0, True),
    ]
]


def test_candidates_exclude_tests_and_sort_by_risk():
    assert [c["path"] for c in candidates(FILES)] == ["a.py", "b.py", "c.py"]


def test_coordinator_output_is_validated_with_fallback():
    client = FakeClient(
        {"coordinator": [plan_json(["b.py", "../evil", "b.py"], [], ["c.py", "a.py", "zzz.py"])]}, agent_route
    )
    plan = make_plan(client, model="m", effort="low", repo_name="r", files=FILES, imported_by={}, per_role=2)
    assert plan.assignments["security"] == ["b.py"]  # unknown paths and duplicates dropped
    assert plan.assignments["tests"] == ["a.py", "b.py"]  # empty -> deterministic fallback
    assert plan.assignments["debt"] == ["c.py", "a.py"]
    assert plan.used_fallback
    sent = client.calls[0]
    assert "test_a.py" not in sent["system"] and "a.py | 10" in sent["system"]  # metrics only, never code
    assert sent["fallbacks"] == "default" and sent["thinking"] == {"type": "adaptive"}


def test_coordinator_garbage_falls_back_entirely():
    client = FakeClient({"coordinator": [response(text("not json"))]}, agent_route)
    plan = make_plan(client, model="m", effort="low", repo_name="r", files=FILES, imported_by={}, per_role=1)
    assert plan.used_fallback and all(v == ["a.py"] for v in plan.assignments.values())


# ---- full run ------------------------------------------------------------------------------


def _setup(tmp_path, fixture_repo):
    settings = replace(
        get_settings(),
        database_url=f"sqlite:///{(tmp_path / 'a.db').as_posix()}",
        allow_local_sources=True,
        agent_max_turns=6,
    )
    sessions = make_session_factory(make_engine(settings.database_url))
    with sessions() as s:
        repo = Repo(source=str(fixture_repo), status="queued")
        s.add(repo)
        s.commit()
        repo_id = repo.id
    execute_ingest(repo_id, settings, sessions, allow_local=True, raise_errors=True)
    with sessions() as s:
        run = AnalysisRun(repo_id=repo_id, model=settings.model)
        s.add(run)
        s.commit()
        run_id = run.id
    return settings, sessions, repo_id, run_id


def test_full_analysis_run_streams_drone_events_and_stores_verified_findings(tmp_path, fixture_repo):
    settings, sessions, repo_id, run_id = _setup(tmp_path, fixture_repo)
    client = FakeClient(
        {
            "coordinator": [plan_json(["pkg/core.py"], ["pkg/util.py"], ["pkg/core.py"])],
            "debt": [
                response(tool("read_file", {"path": "pkg/core.py", "start_line": 1, "end_line": 0})),
                response(
                    tool("submit_finding", GOOD),
                    tool("submit_finding", {**GOOD, "title": "fake", "evidence": "rm -rf /"}),
                ),
                response(text("Checked core.py; one inconsistent return type.")),
            ],
            "tests": [
                response(tool("get_imports", {"path": "pkg/util.py"})),
                response(text("util.helper is only exercised indirectly; nothing worth reporting.")),
            ],
            "security": [response(text("No reachable sinks."), stop="refusal")],
        },
        agent_route,
    )
    results = execute_analysis(run_id, settings, sessions, client=client, allow_local=True)

    by_role = {r.role: r for r in results}
    assert len(by_role["debt"].finding_ids) == 1 and by_role["debt"].rejected == 1
    assert by_role["security"].error  # refusal is reported, not fatal
    with sessions() as s:
        findings = s.scalars(select(Finding).where(Finding.repo_id == repo_id)).all()
        run = s.get(AnalysisRun, run_id)
        types = [e.type for e in s.scalars(select(EventRow).where(EventRow.repo_id == repo_id).order_by(EventRow.seq))]
        seqs = [e.seq for e in s.scalars(select(EventRow).where(EventRow.repo_id == repo_id).order_by(EventRow.seq))]
    assert [f.title for f in findings] == [GOOD["title"]]
    assert findings[0].run_id == run_id and findings[0].agent_id == f"debt-{run_id}"
    assert run.status == "done" and run.input_tokens > 0 and run.plan["security"]["files"] == ["pkg/core.py"]

    analysis = types[types.index("analysis.started") :]
    assert analysis.count("agent.dispatched") == 3 and analysis.count("agent.finished") == 3
    assert "agent.moved" in analysis and "finding.submitted" in analysis
    assert "agent.turn" in analysis and "agent.tool_call" in analysis  # live Mission Control feed
    with sessions() as s:
        calls = [
            e.data
            for e in s.scalars(select(EventRow).where(EventRow.repo_id == repo_id, EventRow.type == "agent.tool_call"))
        ]
        turns = [
            e.data
            for e in s.scalars(select(EventRow).where(EventRow.repo_id == repo_id, EventRow.type == "agent.turn"))
        ]
    rejected = [c for c in calls if c["tool"] == "submit_finding" and not c["ok"]]
    assert len(rejected) == 1 and "evidence was not found" in rejected[0]["note"]
    assert any(c["target"].startswith("pkg/core.py:1-end") for c in calls)
    debt_turns = [t for t in turns if t["agent_id"].startswith("debt")]
    assert [t["turn"] for t in debt_turns] == [1, 2, 3]
    assert debt_turns[-1]["input_tokens"] > debt_turns[0]["input_tokens"]  # cumulative
    # Billed cost travels with the events (fake usage: 100 uncached in / 20 out per turn, Opus 5.5 prices).
    assert debt_turns[-1]["cost_usd"] == pytest.approx(3 * (100 * 4 + 20 * 20) / 1e6)
    assert analysis[-1] == "analysis.completed"
    assert seqs == list(range(1, len(seqs) + 1))  # one gap-free stream across ingest + parallel agents

    # The model never received anything it should not have: tools are strict, history is append-only.
    debt_calls = [c for c in client.calls if agent_route(c) == "debt"]
    assert all(t.get("strict") for t in debt_calls[0]["tools"])
    assert len(debt_calls[2]["messages"]) == 5  # user, assistant, results, assistant, results


def test_usage_prices_cached_input_at_the_cache_rate():
    from types import SimpleNamespace

    from backend.agents.llm import Usage

    u = Usage()
    u.add(
        SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=1_000, cache_creation_input_tokens=2_000, cache_read_input_tokens=10_000, output_tokens=500
            )
        )
    )
    assert u.input_tokens == 13_000  # everything the model read
    # 1k uncached at $4, 2k written at $5, 10k read at $0.20, 500 out at $20 (per million)
    assert u.cost_usd("claude-opus-5-5") == pytest.approx((4_000 + 10_000 + 2_000 + 10_000) / 1e6)
    # The old way priced all 13k input at $4: ~2x too high here, ~5x on a long agent loop.
    assert u.cost_usd("claude-opus-5-5") < (13_000 * 4 + 500 * 20) / 1e6
    assert u.cost_usd("some-unpriced-model") is None
    total = u + u
    assert total.cache_read == 20_000 and total.cost_usd("claude-opus-5-5") == pytest.approx(2 * 0.026)


def test_events_stored_before_cost_tracking_still_load():
    from backend.events.schema import AgentTurn

    old = {
        "run_id": 6, "agent_id": "debt-6", "turn": 26, "max_turns": 30,
        "input_tokens": 1_268_110, "output_tokens": 15_933, "tool_calls": 0, "duration_ms": 8181,
    }  # fmt: skip
    turn = AgentTurn.model_validate(old)
    assert turn.cost_usd is None and turn.cache_read_tokens == 0


def test_analysis_without_credentials_fails_cleanly(tmp_path, fixture_repo, monkeypatch):
    settings, sessions, repo_id, run_id = _setup(tmp_path, fixture_repo)
    from backend.agents import llm

    def no_client():
        raise llm.LLMUnavailable("No Anthropic credentials found.")

    monkeypatch.setattr(llm, "make_client", no_client)
    execute_analysis(run_id, settings, sessions, allow_local=True)
    with sessions() as s:
        run = s.get(AnalysisRun, run_id)
        last = s.scalars(select(EventRow).where(EventRow.repo_id == repo_id).order_by(EventRow.seq.desc())).first()
    assert run.status == "failed" and "credentials" in run.error
    assert last.type == "analysis.failed"


def test_tests_reviewer_also_gets_the_tests_that_import_its_modules():
    imported_by = {"a.py": ["test_a.py", "b.py"], "b.py": ["test_a.py"]}
    client = FakeClient({"coordinator": [plan_json(["a.py"], ["a.py", "b.py"], ["c.py"])]}, agent_route)
    plan = make_plan(client, model="m", effort="low", repo_name="r", files=FILES, imported_by=imported_by, per_role=2)
    assert plan.assignments["tests"] == ["a.py", "b.py", "test_a.py"]  # deduped, appended after the modules
    assert plan.assignments["security"] == ["a.py"]  # other roles unchanged


def test_tests_reviewer_gets_tests_of_other_reviewers_modules_when_its_own_are_untested():
    # Benchmark runs 2 and 3: the tests reviewer got only untested modules, so the weak test file
    # (which imports a module the other reviewers had) was never attached and its bug was missed.
    imported_by = {"a.py": ["test_a.py"]}
    client = FakeClient({"coordinator": [plan_json(["a.py"], ["b.py"], ["c.py"])]}, agent_route)
    plan = make_plan(client, model="m", effort="low", repo_name="r", files=FILES, imported_by=imported_by, per_role=2)
    assert plan.assignments["tests"] == ["b.py", "test_a.py"]
    assert plan.assignments["security"] == ["a.py"]
