"""Tools the specialist agents can call, and the code that executes them.

The model only ever *judges*; every fact it sees (lines, metrics, imports) comes from here.
`submit_finding` is verified deterministically before it is stored: the file must exist,
the line range must be real, and the quoted evidence must appear at those lines.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .workspace import MAX_READ_LINES, Workspace, WorkspaceError, evidence_in_lines

ROLES = ("security", "tests", "debt")
SEVERITIES = ("critical", "high", "medium", "low")


def _strict(name: str, description: str, properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


READ_FILE = _strict(
    "read_file",
    f"Read lines from a file in the repository, with line numbers. At most {MAX_READ_LINES} lines per call; "
    "use end_line 0 to read to the end (still capped). Line numbers are 1-based.",
    {
        "path": {"type": "string", "description": "Repo-relative path, e.g. src/app/views.py"},
        "start_line": {"type": "integer"},
        "end_line": {"type": "integer"},
    },
)

SEARCH_CODE = _strict(
    "search_code",
    "Regex search (Python re syntax) over every ingested file, line by line. Returns path:line: text. "
    "Use path_glob to narrow, e.g. 'src/**/*.py' or '*' for everything.",
    {"pattern": {"type": "string"}, "path_glob": {"type": "string"}},
)

FILE_METRICS = _strict(
    "get_file_metrics",
    "Deterministic metrics for a file: lines of code, functions, cyclomatic complexity (total and max), "
    "commits in the last 30/90 days, test coverage if known, hotspot risk, and whether it is a test file.",
    {"path": {"type": "string"}},
)

FILE_IMPORTS = _strict(
    "get_imports",
    "Import graph neighbours of a file inside the repo: which files it imports and which files import it. "
    "Useful to find a module's tests or its callers.",
    {"path": {"type": "string"}},
)


def submit_finding_tool(role: str) -> dict[str, Any]:
    return _strict(
        "submit_finding",
        "Record one concrete, verified problem. `evidence` must be copied exactly from the file at "
        "line_start..line_end (it is checked; a mismatch is rejected and you can resubmit). "
        "One finding per distinct problem. confidence is 0.0-1.0.",
        {
            "category": {"type": "string", "enum": [role]},
            "severity": {"type": "string", "enum": list(SEVERITIES)},
            "file": {"type": "string"},
            "line_start": {"type": "integer"},
            "line_end": {"type": "integer"},
            "title": {"type": "string", "description": "Short, specific: what is wrong, not where."},
            "evidence": {"type": "string", "description": "Exact code quoted from the file."},
            "explanation": {"type": "string", "description": "Why this is a problem and what can go wrong."},
            "suggested_fix": {"type": "string"},
            "confidence": {"type": "number"},
        },
    )


def specialist_tools(role: str) -> list[dict[str, Any]]:
    # Order is fixed so the tools prefix stays byte-identical across requests (prompt caching).
    return [READ_FILE, SEARCH_CODE, FILE_METRICS, FILE_IMPORTS, submit_finding_tool(role)]


@dataclass
class ToolResult:
    content: str
    is_error: bool = False
    moved_to: str | None = None  # file the agent is now looking at, for the drone
    action: str | None = None


@dataclass
class AcceptedFinding:
    category: str
    severity: str
    file: str
    line_start: int
    line_end: int
    title: str
    evidence: str
    explanation: str
    suggested_fix: str
    confidence: float
    needs_human_review: bool


# Security and debt reviewers often spot the same bug from different angles; a test-gap finding
# about the same lines is a different problem ("this is untested"), so it never counts as a duplicate.
OVERLAPPING_ROLES = {"security", "debt"}


class PossibleDuplicate(Exception):
    """Every stored finding the submission overlaps, as (id, source, title). All of them are named at
    once: a resubmission is the agent saying "different from these", so it must have seen them all.
    (Live run 11: only the first overlap was named, the agent rightly said "different", and the
    resubmission skipped past an exact repeat further down the list.)"""

    def __init__(self, matches: list[tuple[int, str, str]]) -> None:
        super().__init__(", ".join(f"#{m[0]}" for m in matches))
        self.matches = matches


# Earlier runs' findings that a rerun should not repeat. Dismissed ones count: a human already said
# no. Fixed ones don't: if the problem is still in the code, reporting it again is right.
PRIOR_STATUSES = ("open", "confirmed", "rejected")
_PRIOR_LABEL = {"open": "open", "confirmed": "confirmed by a human", "rejected": "dismissed by a human"}


Scope = tuple[int, int] | None  # lines of the innermost enclosing function; None at module level


class FindingRegistry:
    """Shared by all agents in one run, so the same problem isn't stored twice.

    Two findings may be the same problem when their lines overlap, or, within one category, when
    they sit in the same function (live run 12: a repeat of #90 cited line 285 instead of 297-307 of
    the same function and was stored). Neither proves it (a validation gap and a crash can share a
    line), so the registry only flags; the agent decides, and a deliberate resubmission is stored.
    `scope` maps (file, start, end) to the enclosing function's lines; without it, lines only.
    """

    def __init__(self, scope: Callable[[str, int, int], Scope] | None = None) -> None:
        self._lock = threading.Lock()
        self._scope = scope or (lambda _f, _s, _e: None)
        # category, file, start, end, scope, id, title, source (who reported it, for the agent's message)
        self._entries: list[tuple[str, str, int, int, Scope, int, str, str]] = []

    def seed_prior(self, rows: list[dict[str, Any]]) -> None:
        """Load earlier runs' findings (dicts with id, category, file, line_start, line_end, title,
        status), so a rerun is told about them instead of storing the same problem again."""
        with self._lock:
            for r in rows:
                if r["status"] not in PRIOR_STATUSES:
                    continue
                source = f"an earlier run ({_PRIOR_LABEL[r['status']]})"
                file, start, end = r["file"], r["line_start"], r["line_end"]
                scope = self._scope(file, start, end)
                self._entries.append((r["category"], file, start, end, scope, r["id"], r["title"], source))

    @staticmethod
    def overlaps(a: tuple[str, str, int, int, Scope], b: tuple[str, str, int, int, Scope]) -> bool:
        cat_a, file_a, s_a, e_a, scope_a = a
        cat_b, file_b, s_b, e_b, scope_b = b
        if file_a != file_b:
            return False
        if s_a <= e_b + 1 and s_b <= e_a + 1:  # the lines overlap
            return cat_a == cat_b or {cat_a, cat_b} <= OVERLAPPING_ROLES
        # Same function, same category. Not across security/debt: they often report different
        # problems in one function, and each false flag costs the agent a turn.
        return cat_a == cat_b and scope_a is not None and scope_a == scope_b

    def claim(self, f: AcceptedFinding, store: Callable[[AcceptedFinding], int], *, force: bool = False) -> int:
        """Store `f` unless it overlaps an existing finding (and isn't forced). Check-and-store is atomic."""
        key = (f.category, f.file, f.line_start, f.line_end, self._scope(f.file, f.line_start, f.line_end))
        with self._lock:
            if not force:
                matches = [
                    (fid, source, title)
                    for cat, file, start, end, scope, fid, title, source in self._entries
                    if self.overlaps(key, (cat, file, start, end, scope))
                ]
                if matches:
                    raise PossibleDuplicate(matches)
            fid = store(f)
            self._entries.append((*key, fid, f.title, f"the {f.category} reviewer"))
            return fid


def _joins_two(title: str, category: str) -> bool:
    """A title that may hold two problems. ';' always; ' and ' only for bugs, where shop run 15 merged
    "expiry never enforced and signature compared non-constant-time". Test-gap titles name several
    untested things with 'and' as a matter of course, so asking there would only cost turns."""
    return ";" in title or (category in ("security", "debt") and " and " in title.lower())


@dataclass
class ToolBox:
    """Executes tool calls for one agent. `on_finding` stores an accepted finding and returns its id."""

    role: str
    workspace: Workspace
    metrics: dict[str, dict[str, Any]]
    imports: dict[str, list[str]]
    imported_by: dict[str, list[str]]
    on_finding: Callable[[AcceptedFinding], int]
    registry: FindingRegistry = field(default_factory=FindingRegistry)
    accepted: list[int] = field(default_factory=list)
    rejected: int = 0
    duplicates: int = 0
    _flagged: set[tuple[str, int, int, str]] = field(default_factory=set)
    _split_asked: set[tuple[str, int, int, str]] = field(default_factory=set)  # kept apart from _flagged
    _seen: set[tuple[str, int, str]] = field(default_factory=set)

    def run(self, name: str, args: dict[str, Any]) -> ToolResult:
        try:
            handler = getattr(self, f"_t_{name}", None)
            if handler is None:
                return ToolResult(f"Unknown tool: {name}", is_error=True)
            return handler(**args)
        except WorkspaceError as exc:
            return ToolResult(str(exc), is_error=True)
        except TypeError as exc:  # missing/extra arguments
            return ToolResult(f"Bad arguments for {name}: {exc}", is_error=True)

    def _t_read_file(self, path: str, start_line: int = 1, end_line: int = 0) -> ToolResult:
        rel = self.workspace.normalize(path)
        return ToolResult(self.workspace.read(rel, start_line, end_line), moved_to=rel, action="read")

    def _t_search_code(self, pattern: str, path_glob: str = "*") -> ToolResult:
        out = self.workspace.search(pattern, path_glob)
        first = out.split(":", 1)[0] if out != "No matches." else None
        return ToolResult(out, moved_to=first if first in self.metrics else None, action="search")

    def _t_get_file_metrics(self, path: str) -> ToolResult:
        rel = self.workspace.normalize(path)
        m = self.metrics.get(rel)
        if m is None:
            raise WorkspaceError(f"Unknown file: {rel!r}")
        return ToolResult(json.dumps(m, sort_keys=True), moved_to=rel, action="metrics")

    def _t_get_imports(self, path: str) -> ToolResult:
        rel = self.workspace.normalize(path)
        if rel not in self.metrics:
            raise WorkspaceError(f"Unknown file: {rel!r}")
        data = {"imports": self.imports.get(rel, []), "imported_by": self.imported_by.get(rel, [])}
        return ToolResult(json.dumps(data, sort_keys=True), moved_to=rel, action="imports")

    def _t_submit_finding(self, **f: Any) -> ToolResult:
        problems = self.verify(f)
        if problems:
            self.rejected += 1
            return ToolResult("Finding rejected: " + "; ".join(problems) + ". Fix and resubmit.", is_error=True)
        rel = self.workspace.normalize(f["file"])
        key = (rel, int(f["line_start"]), f["title"].strip().lower())
        if key in self._seen:
            return ToolResult("Duplicate of a finding you already submitted; not stored.", is_error=True)
        conf = float(f["confidence"])
        finding = AcceptedFinding(
            category=f["category"],
            severity=f["severity"],
            file=rel,
            line_start=int(f["line_start"]),
            line_end=int(f["line_end"]),
            title=f["title"].strip()[:300],
            evidence=f["evidence"],
            explanation=f["explanation"],
            suggested_fix=f["suggested_fix"],
            confidence=conf,
            # Low-confidence or critical calls get a human look before anyone acts on them.
            needs_human_review=conf < 0.6 or f["severity"] == "critical",
        )
        attempt = (rel, finding.line_start, finding.line_end, finding.title.lower())
        if _joins_two(finding.title, finding.category) and attempt not in self._split_asked:
            # Benchmark runs 9 and 11: Sonnet joined two planted bugs in one title ("expiry never
            # enforced; signature compared non-constant-time"), and whoever fixes the first never sees
            # the second. Asked once; a resubmission is the agent saying it is one problem.
            self._split_asked.add(attempt)
            return ToolResult(
                "Not stored yet: the title joins two statements with ';', which usually means two problems "
                "in one finding. If so, submit each as its own finding with its own lines and evidence. If it "
                "really is one problem, submit it again unchanged.",
                is_error=True,
            )
        try:
            fid = self.registry.claim(finding, self.on_finding, force=attempt in self._flagged)
        except PossibleDuplicate as d:
            self._flagged.add(attempt)
            self.duplicates += 1
            listed = "; ".join(f"#{fid} from {source}: {title!r}" for fid, source, title in d.matches)
            return ToolResult(
                f"Not stored yet: these findings cover the same lines or function: {listed}. If yours is the same "
                "problem as any of them, move on. If it is different from all of them, submit it again "
                "unchanged and it will be stored.",
                is_error=True,
            )
        self._seen.add(key)  # only once actually stored, so a flagged overlap can still be resubmitted
        self.accepted.append(fid)
        return ToolResult(f"Finding #{fid} recorded.", moved_to=rel, action="read")

    def verify(self, f: dict[str, Any]) -> list[str]:
        problems: list[str] = []
        if f.get("category") != self.role:
            problems.append(f"category must be {self.role!r} for this agent")
        if f.get("severity") not in SEVERITIES:
            problems.append(f"severity must be one of {', '.join(SEVERITIES)}")
        try:
            conf = float(f.get("confidence", -1))
        except (TypeError, ValueError):
            conf = -1
        if not 0.0 <= conf <= 1.0:
            problems.append("confidence must be between 0.0 and 1.0")
        for k in ("title", "evidence", "explanation", "suggested_fix"):
            if not str(f.get(k, "")).strip():
                problems.append(f"{k} is empty")
        try:
            lines = self.workspace.lines(str(f.get("file", "")))
        except WorkspaceError as exc:
            return [*problems, str(exc)]
        try:
            start, end = int(f["line_start"]), int(f["line_end"])
        except (KeyError, TypeError, ValueError):
            return [*problems, "line_start and line_end must be integers"]
        if not 1 <= start <= end <= len(lines):
            problems.append(f"line range {start}-{end} is not within the file (1-{len(lines)})")
        elif not evidence_in_lines(str(f.get("evidence", "")), lines, start, end):
            problems.append(
                f"evidence was not found at lines {start}-{end}; quote the code exactly as it appears there"
            )
        return problems
