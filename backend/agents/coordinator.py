"""The coordinator: decides which specialist looks at which files, from metrics alone (ADR 0001).

It never sees code. Its output is validated against the candidate list, and any role it leaves
empty (or a failed call) falls back to the deterministic top hotspots, so a run always proceeds.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from . import llm
from .tools import ROLES

MAX_CANDIDATES = 40
# Roles whose unused slots are filled with the next riskiest candidates. Debt's brief ("highest
# complexity and churn") is what the risk score already ranks, so an under-filled pick only loses
# coverage: benchmark run 7 gave debt 3 of 5 slots and skipped cache.py, missing both bugs in it.
# Security picks depend on what a file does, not its metrics, so it isn't topped up.
TOP_UP_ROLES = ("debt",)


@dataclass
class Plan:
    assignments: dict[str, list[str]]
    reasons: dict[str, str]
    used_fallback: bool = False
    usage: llm.Usage = field(default_factory=llm.Usage)

    def to_json(self) -> dict[str, Any]:
        return {
            role: {"files": self.assignments.get(role, []), "reason": self.reasons.get(role, "")} for role in ROLES
        } | {"used_fallback": self.used_fallback}


def candidates(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Non-test, non-empty files, riskiest first. Deterministic; ties broken by path."""
    pool = [f for f in files if not f["is_test"] and f["loc"] > 0]
    pool.sort(key=lambda f: (-f["risk"], f["path"]))
    return pool[:MAX_CANDIDATES]


def default_plan(cands: list[dict[str, Any]], per_role: int) -> Plan:
    """Top hotspots for every role. Callers add the tests reviewer's test files afterwards."""
    top = [c["path"] for c in cands[:per_role]]
    return Plan(
        assignments={r: list(top) for r in ROLES},
        reasons={r: "Top hotspots by risk (deterministic fallback)." for r in ROLES},
        used_fallback=True,
    )


def _table(cands: list[dict[str, Any]], imported_by: dict[str, list[str]], tests: set[str]) -> str:
    rows = ["path | loc | functions | ccn_total | ccn_max | commits_90d | risk | importers | test_importers"]
    for c in cands:
        imp = imported_by.get(c["path"], [])
        rows.append(
            f"{c['path']} | {c['loc']} | {c['functions']} | {c['complexity_total']} | {c['complexity_max']} | "
            f"{c['churn_90']} | {c['risk']:.2f} | {len(imp)} | {sum(1 for p in imp if p in tests)}"
        )
    return "\n".join(rows)


def _schema() -> dict[str, Any]:
    role = {
        "type": "object",
        "properties": {"files": {"type": "array", "items": {"type": "string"}}, "reason": {"type": "string"}},
        "required": ["files", "reason"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {r: role for r in ROLES},
        "required": list(ROLES),
        "additionalProperties": False,
    }


def make_plan(
    client: Any,
    *,
    model: str,
    effort: str,
    repo_name: str,
    files: list[dict[str, Any]],
    imported_by: dict[str, list[str]],
    per_role: int,
) -> Plan:
    cands = candidates(files)
    if not cands:
        return Plan(assignments={r: [] for r in ROLES}, reasons={r: "No candidate files." for r in ROLES})
    tests = {f["path"] for f in files if f["is_test"]}
    system = llm.prompt("coordinator", repo=repo_name, per_role=str(per_role), table=_table(cands, imported_by, tests))
    usage = llm.Usage()
    try:
        response = llm.create(
            client,
            model=model,
            effort=effort,
            max_tokens=8000,
            system=system,
            messages=[{"role": "user", "content": "Make the assignments."}],
            output_config={"format": {"type": "json_schema", "schema": _schema()}},
        )
        usage.add(response)
        if response.stop_reason == "refusal":
            raise ValueError("coordinator declined")
        raw = json.loads(llm.text_of(response))
    except llm.LLMUnavailable:
        raise
    except Exception:
        plan = default_plan(cands, per_role)
        plan.usage = usage
        add_tests_for_assigned(plan, files, imported_by)
        return plan

    valid = {c["path"] for c in cands}
    fallback = default_plan(cands, per_role)
    assignments: dict[str, list[str]] = {}
    reasons: dict[str, str] = {}
    used_fallback = False
    for role in ROLES:
        entry = raw.get(role) or {}
        picked: list[str] = []
        for p in entry.get("files", []):
            if p in valid and p not in picked:
                picked.append(p)
        if picked:
            assignments[role] = picked[:per_role]
            reasons[role] = str(entry.get("reason", ""))[:500]
            if role in TOP_UP_ROLES and len(picked) < per_role:
                assignments[role] += [c["path"] for c in cands if c["path"] not in picked][: per_role - len(picked)]
        else:
            assignments[role] = fallback.assignments[role]
            reasons[role] = fallback.reasons[role]
            used_fallback = True
    plan = Plan(assignments, reasons, used_fallback, usage)
    add_tests_for_assigned(plan, files, imported_by)
    return plan


MAX_TEST_FILES = 3


def add_tests_for_assigned(plan: Plan, files: list[dict[str, Any]], imported_by: dict[str, list[str]]) -> None:
    """Give the tests reviewer the test files that exercise the modules under review.

    Candidates exclude test files (they'd crowd out real hotspots), so without this the reviewer
    could never see a test that can't fail. The coordinator tends to give the tests reviewer
    *untested* modules, whose importers are by definition not tests, so the tests of every other
    reviewer's modules are attached too: the tests reviewer's own modules first, then the rest
    in role order. Deterministic: straight from the import graph.
    """
    tests = {f["path"] for f in files if f["is_test"]}
    assigned = plan.assignments.get("tests", [])
    modules = list(assigned) + [p for r in ROLES if r != "tests" for p in plan.assignments.get(r, [])]
    extra: list[str] = []
    for path in modules:
        for importer in imported_by.get(path, []):
            if importer in tests and importer not in extra and importer not in assigned:
                extra.append(importer)
    plan.assignments["tests"] = assigned + extra[:MAX_TEST_FILES]
