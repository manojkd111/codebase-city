from __future__ import annotations

from evals.benchmark.score import hotspot_recall, load_planted, score


def test_every_planted_anchor_resolves_to_exactly_one_line():
    planted = load_planted()
    assert len(planted) == 13 and all(p.line > 0 for p in planted)


def test_scoring_is_one_to_one_and_category_aware():
    planted = load_planted()
    sqli = next(p for p in planted if p.id == "sqli")
    findings = [
        {"file": sqli.file, "category": "security", "line_start": sqli.line, "line_end": sqli.line},
        {"file": sqli.file, "category": "security", "line_start": sqli.line - 1, "line_end": sqli.line},  # dup
        {"file": sqli.file, "category": "debt", "line_start": sqli.line, "line_end": sqli.line},  # wrong category
        {"file": "app/pricing.py", "category": "tests", "line_start": 200, "line_end": 210},  # nowhere near
    ]
    s = score(findings, planted)
    assert s.matched == {"sqli": 0}
    assert s.unmatched == [1, 2, 3]
    assert s.recall == 1 / 13 and s.precision_floor == 1 / 4


def test_known_real_anchors_resolve():
    known = load_planted(key="known_real")
    assert len(known) == 7 and all(k.line > 0 for k in known)


def test_known_real_counts_for_precision_not_recall_and_never_steals_a_planted_match():
    planted = load_planted()
    known = load_planted(key="known_real")
    unauth = next(p for p in planted if p.id == "unauth-checkout")
    stored = next(k for k in known if k.id == "stored-pre-discount")
    conn = next(k for k in known if k.id == "shared-conn")  # no other problem within SLACK of it
    # The first finding is within SLACK of both; planted problems are matched first, so it goes to unauth.
    findings = [
        {"file": unauth.file, "category": "debt", "line_start": stored.line, "line_end": stored.line},
        {"file": unauth.file, "category": "security", "line_start": unauth.line, "line_end": unauth.line},
        {"file": conn.file, "category": "debt", "line_start": conn.line, "line_end": conn.line},
        {"file": conn.file, "category": "debt", "line_start": conn.line, "line_end": conn.line},  # duplicate
    ]
    s = score(findings, planted, known)
    assert s.matched == {"unauth-checkout": 0}
    assert s.known == {"stored-pre-discount": 1, "shared-conn": 2}
    assert s.recall == 1 / 13
    assert s.unmatched == [3]  # a duplicate of a known problem is still noise
    assert s.precision_floor == 3 / 4


def test_hotspot_recall():
    planted = load_planted()
    files = [
        {"path": "app/db.py", "risk": 9, "is_test": False},
        {"path": "app/x.py", "risk": 8, "is_test": False},
        {"path": "tests/test_db.py", "risk": 99, "is_test": True},
    ]
    targets = {p.file for p in planted if not p.file.startswith("tests/")}
    assert hotspot_recall(files, planted, 1) == 1 / len(targets)


def test_benchmark_metrics_only_runs_end_to_end(capsys):
    from evals.benchmark.run import main

    assert main(["--metrics-only"]) == 0
    assert "hotspot recall" in capsys.readouterr().out


def test_tool_selection_cases_reference_real_tools_and_args():
    import asyncio
    import json

    from evals.tool_selection.run import HERE, _mcp_tools, grade, to_claude_tools

    tools = {t["name"]: t for t in to_claude_tools(asyncio.run(_mcp_tools()))}
    assert len(tools) == 11
    for t in tools.values():  # what Claude actually receives: no pydantic titles, a real description
        assert t["description"] and "title" not in t["input_schema"]
    cases = json.loads((HERE / "cases.json").read_text())["cases"]
    for c in cases:
        assert c["expected"] in tools, c
        assert set(c.get("args", {})) <= set(tools[c["expected"]]["input_schema"]["properties"]), c

    case = {"expected": "get_hotspots", "args": {"repo_id": 3}}
    assert grade(case, "get_hotspots", {"repo_id": 3, "limit": 10}) == (True, True)
    assert grade(case, "get_hotspots", {"repo_id": 4}) == (True, False)
    assert grade(case, "list_repos", {}) == (False, False)
    assert grade({**case, "any_of": ["list_repos"]}, "list_repos", {}) == (True, True)


def test_span_lets_a_function_wide_problem_match_inside_the_body():
    planted = load_planted()
    exp = next(p for p in planted if p.id == "no-expiry")
    assert exp.span == 9
    inside = {"file": exp.file, "category": "debt", "line_start": exp.line + 5, "line_end": exp.line + 5}
    after = {"file": exp.file, "category": "debt", "line_start": exp.line + 14, "line_end": exp.line + 14}
    assert score([inside], [exp]).matched == {"no-expiry": 0}
    assert score([after], [exp]).matched == {}
