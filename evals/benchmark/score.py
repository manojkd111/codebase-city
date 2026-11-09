"""Score agent findings against the planted problems. Pure code, no model calls."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
SLACK = 3
ALL_CATEGORIES = ["security", "tests", "debt"]
FIXTURES = {"shop": HERE / "expected.json", "ledger": HERE / "expected_ledger.json"}


@dataclass
class Planted:
    id: str
    file: str
    line: int
    categories: list[str]
    what: str
    span: int = 0  # lines after the anchor that also belong to the problem (e.g. a whole function body)
    # Other places the same problem can rightly be cited, as (file, line, span): a cross-file bug
    # (say, tax keys vs how regions are stored) is found whichever end the finding points at.
    alts: list[tuple[str, int, int]] = field(default_factory=list)

    def locations(self) -> list[tuple[str, int, int]]:
        return [(self.file, self.line, self.span), *self.alts]


@dataclass
class Score:
    matched: dict[str, int] = field(default_factory=dict)  # planted id -> finding index
    missed: list[str] = field(default_factory=list)
    known: dict[str, int] = field(default_factory=dict)  # known-real id -> finding index (precision only)
    unmatched: list[int] = field(default_factory=list)  # finding indexes that hit nothing planted or known
    decoy_hits: dict[str, list[int]] = field(default_factory=dict)  # decoy id -> unmatched findings on it
    total_planted: int = 0
    total_findings: int = 0

    @property
    def recall(self) -> float:
        return len(self.matched) / self.total_planted if self.total_planted else 0.0

    @property
    def precision_floor(self) -> float:
        """Lower bound: unmatched findings may still be real problems nobody has listed yet."""
        correct = len(self.matched) + len(self.known)
        return correct / self.total_findings if self.total_findings else 0.0


def load_planted(
    expected_path: Path = HERE / "expected.json", root: Path | None = None, key: str = "planted"
) -> list[Planted]:
    """`key="known_real"` loads the unplanted-but-real problems instead, `key="decoys"` the code that
    looks wrong but isn't (same shape; a decoy without categories counts findings of any category)."""
    spec = json.loads(expected_path.read_text(encoding="utf-8"))
    root = root or expected_path.parent / spec["repo"]

    def anchor_line(pid: str, file: str, anchor: str) -> int:
        lines = (root / file).read_text(encoding="utf-8").splitlines()
        hits = [i for i, line in enumerate(lines, 1) if anchor in line]
        if len(hits) != 1:
            raise ValueError(f"anchor for {pid!r} matches {len(hits)} lines in {file}; it must match one")
        return hits[0]

    out = []
    for p in spec.get(key, []):
        alts = [(a["file"], anchor_line(p["id"], a["file"], a["anchor"]), a.get("span", 0)) for a in p.get("also", [])]
        line = anchor_line(p["id"], p["file"], p["anchor"])
        cats = p.get("categories", ALL_CATEGORIES)
        out.append(Planted(p["id"], p["file"], line, cats, p["what"], p.get("span", 0), alts))
    return out


def fixture_spec(expected_path: Path) -> dict[str, Any]:
    return json.loads(expected_path.read_text(encoding="utf-8"))


def _candidates(findings: list[dict[str, Any]], p: Planted, taken: set[int]) -> list[int]:
    out = []
    for i, f in enumerate(findings):
        if i in taken or f["category"] not in p.categories:
            continue
        # Overlap between the finding (± SLACK) and the problem's lines, at any of its locations.
        if any(
            f["file"] == file and f["line_start"] - SLACK <= line + span and line <= f["line_end"] + SLACK
            for file, line, span in p.locations()
        ):
            out.append(i)
    return out


def _max_match(findings: list[dict[str, Any]], problems: list[Planted], taken: set[int]) -> dict[str, int]:
    """Maximum one-to-one matching (augmenting paths), so one finding near two problems can't
    starve the other: e.g. a finding spanning lines 8-12 is within SLACK of a problem on line 5
    and one on line 11, while a second finding only reaches line 5. Greedy matching would give
    the first finding to line 5 and miss line 11."""
    cands = {p.id: _candidates(findings, p, taken) for p in problems}
    owner: dict[int, str] = {}  # finding index -> problem id

    def augment(pid: str, seen: set[int]) -> bool:
        for i in cands[pid]:
            if i in seen:
                continue
            seen.add(i)
            if i not in owner or augment(owner[i], seen):
                owner[i] = pid
                return True
        return False

    for p in problems:
        augment(p.id, set())
    return {pid: i for i, pid in owner.items()}


def score(
    findings: list[dict[str, Any]], planted: list[Planted], known: list[Planted] = (), decoys: list[Planted] = ()
) -> Score:
    """One-to-one matching: each problem is credited to at most one finding, and as many problems
    as possible are credited.

    Planted problems are matched first, so a known-real problem can never take a finding from one.
    """
    s = Score(total_planted=len(planted), total_findings=len(findings))
    matched = _max_match(findings, planted, set())
    s.matched = {p.id: matched[p.id] for p in planted if p.id in matched}
    s.missed = [p.id for p in planted if p.id not in matched]
    used = set(matched.values())
    found = _max_match(findings, list(known), used)
    s.known = {k.id: found[k.id] for k in known if k.id in found}
    used |= set(found.values())
    s.unmatched = [i for i in range(len(findings)) if i not in used]
    # A false alarm on code that only looks dangerous. Several findings can hit one decoy; each is noise.
    for d in decoys:
        hits = [i for i in s.unmatched if i in _candidates(findings, d, set())]
        if hits:
            s.decoy_hits[d.id] = hits
    return s


def hotspot_recall(files: list[dict[str, Any]], planted: list[Planted], k: int) -> float:
    """Share of non-test files with a planted problem that rank in the top-k by risk, out of as many
    as could fit (min(k, files)), so a fixture with more buggy files than k can still score 100%.

    This checks the deterministic half (ADR 0001): if the risk score never surfaces a file,
    the coordinator can't assign it.
    """
    ranked = [f["path"] for f in sorted(files, key=lambda f: (-f["risk"], f["path"])) if not f["is_test"]][:k]
    targets = {p.file for p in planted if not p.file.startswith("tests/")}
    return len(targets & set(ranked)) / min(k, len(targets)) if targets else 0.0
