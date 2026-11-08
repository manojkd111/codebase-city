"""Score agent findings against the planted problems. Pure code, no model calls."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
SLACK = 3


@dataclass
class Planted:
    id: str
    file: str
    line: int
    categories: list[str]
    what: str
    span: int = 0  # lines after the anchor that also belong to the problem (e.g. a whole function body)


@dataclass
class Score:
    matched: dict[str, int] = field(default_factory=dict)  # planted id -> finding index
    missed: list[str] = field(default_factory=list)
    unmatched: list[int] = field(default_factory=list)  # finding indexes that hit nothing planted
    total_planted: int = 0
    total_findings: int = 0

    @property
    def recall(self) -> float:
        return len(self.matched) / self.total_planted if self.total_planted else 0.0

    @property
    def precision_floor(self) -> float:
        """Lower bound: unmatched findings may still be real problems the fixture didn't plant."""
        return len(self.matched) / self.total_findings if self.total_findings else 0.0


def load_planted(expected_path: Path = HERE / "expected.json", root: Path | None = None) -> list[Planted]:
    spec = json.loads(expected_path.read_text(encoding="utf-8"))
    root = root or expected_path.parent / spec["repo"]
    out = []
    for p in spec["planted"]:
        lines = (root / p["file"]).read_text(encoding="utf-8").splitlines()
        hits = [i for i, line in enumerate(lines, 1) if p["anchor"] in line]
        if len(hits) != 1:
            raise ValueError(f"anchor for {p['id']!r} matches {len(hits)} lines in {p['file']}; it must match one")
        out.append(Planted(p["id"], p["file"], hits[0], p["categories"], p["what"], p.get("span", 0)))
    return out


def score(findings: list[dict[str, Any]], planted: list[Planted]) -> Score:
    """Greedy one-to-one matching: each planted problem is credited to at most one finding."""
    s = Score(total_planted=len(planted), total_findings=len(findings))
    used: set[int] = set()
    for p in planted:
        for i, f in enumerate(findings):
            if i in used or f["file"] != p.file or f["category"] not in p.categories:
                continue
            # Overlap between the finding (± SLACK) and the planted problem's lines.
            if f["line_start"] - SLACK <= p.line + p.span and p.line <= f["line_end"] + SLACK:
                s.matched[p.id] = i
                used.add(i)
                break
        else:
            s.missed.append(p.id)
    s.unmatched = [i for i in range(len(findings)) if i not in used]
    return s


def hotspot_recall(files: list[dict[str, Any]], planted: list[Planted], k: int) -> float:
    """Share of non-test files with a planted problem that rank in the top-k by risk.

    This checks the deterministic half (ADR 0001): if the risk score never surfaces a file,
    the coordinator can't assign it.
    """
    ranked = [f["path"] for f in sorted(files, key=lambda f: (-f["risk"], f["path"])) if not f["is_test"]][:k]
    targets = {p.file for p in planted if not p.file.startswith("tests/")}
    return len(targets & set(ranked)) / len(targets) if targets else 0.0
