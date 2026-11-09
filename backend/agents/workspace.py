"""Read-only, path-safe access to a checked-out repo for agents.

Everything an agent sees goes through here, so the rules live in one place:
paths must stay inside the repo, symlinks are never followed, and output is capped.
Nothing here executes repo code (ADR 0003).
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

MAX_READ_LINES = 400
MAX_SEARCH_HITS = 60
MAX_LINE_CHARS = 400


class WorkspaceError(ValueError):
    """A tool input the workspace refuses (bad path, bad range, bad regex). Shown to the model."""


@dataclass
class Workspace:
    root: Path
    files: list[str]  # repo-relative posix paths that were ingested; nothing else is readable

    def __post_init__(self) -> None:
        self.root = self.root.resolve()
        self._known = set(self.files)
        self._cache: dict[str, list[str]] = {}
        self._spans: dict[str, list[tuple[int, int]]] = {}  # function line spans, for duplicate checks

    # -- paths -------------------------------------------------------------------------------

    def normalize(self, path: str) -> str:
        p = PurePosixPath(path.replace("\\", "/").lstrip("/"))
        if any(part in ("..", "") for part in p.parts) or p.is_absolute():
            raise WorkspaceError(f"Invalid path: {path!r}")
        return str(p)

    def _resolve(self, path: str) -> tuple[str, Path]:
        rel = self.normalize(path)
        if rel not in self._known:
            hint = self._suggest(rel)
            raise WorkspaceError(f"Unknown file: {rel!r}." + (f" Did you mean {hint!r}?" if hint else ""))
        full = self.root / rel
        # Never follow symlinks out of the clone, at any level of the path.
        cur = self.root
        for part in PurePosixPath(rel).parts:
            cur = cur / part
            if cur.is_symlink():
                raise WorkspaceError(f"Refusing to follow symlink: {rel!r}")
        if not full.resolve().is_relative_to(self.root):
            raise WorkspaceError(f"Path escapes the repository: {rel!r}")
        return rel, full

    def _suggest(self, rel: str) -> str | None:
        name = PurePosixPath(rel).name
        same = [f for f in self.files if PurePosixPath(f).name == name]
        return same[0] if len(same) == 1 else None

    # -- reading -----------------------------------------------------------------------------

    def lines(self, path: str) -> list[str]:
        rel, full = self._resolve(path)
        if rel not in self._cache:
            text = full.read_bytes().decode("utf-8", "replace")
            self._cache[rel] = text.splitlines()
        return self._cache[rel]

    def enclosing_function(self, path: str, start: int, end: int) -> tuple[int, int] | None:
        """Lines of the innermost function holding `start`-`end` (or, failing that, `start`).

        Parsed by lizard, never executed. None at module level, or for a file that can't be read.
        """
        try:
            rel = self.normalize(path)
            if rel not in self._spans:
                from backend.ingest.complexity import function_spans

                self._spans[rel] = function_spans(rel, "\n".join(self.lines(rel)) + "\n")
        except (WorkspaceError, OSError):
            return None
        spans = self._spans[rel]
        holding = [s for s in spans if s[0] <= start and end <= s[1]] or [s for s in spans if s[0] <= start <= s[1]]
        return min(holding, key=lambda s: s[1] - s[0]) if holding else None

    def read(self, path: str, start: int = 1, end: int | None = None) -> str:
        lines = self.lines(path)
        n = len(lines)
        if n == 0:
            return f"{self.normalize(path)} is empty."
        start = max(1, start)
        end = n if end is None or end <= 0 else min(end, n)
        if start > n:
            raise WorkspaceError(f"start_line {start} is past the end of the file ({n} lines).")
        if end < start:
            raise WorkspaceError(f"end_line {end} is before start_line {start}.")
        end = min(end, start + MAX_READ_LINES - 1)
        width = len(str(end))
        body = "\n".join(f"{i:>{width}}| {self._clip(lines[i - 1])}" for i in range(start, end + 1))
        more = f"\n... ({n - end} more lines; read again with start_line={end + 1})" if end < n else ""
        return f"{self.normalize(path)} lines {start}-{end} of {n}\n{body}{more}"

    def search(self, pattern: str, path_glob: str = "*") -> str:
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            raise WorkspaceError(f"Invalid regex: {exc}") from exc
        hits: list[str] = []
        files_hit = 0
        for f in self.files:
            if not fnmatch.fnmatch(f, path_glob or "*"):
                continue
            try:
                lines = self.lines(f)
            except (WorkspaceError, OSError):
                continue
            matched = False
            for i, line in enumerate(lines, 1):
                if rx.search(line):
                    matched = True
                    hits.append(f"{f}:{i}: {self._clip(line.strip())}")
                    if len(hits) >= MAX_SEARCH_HITS:
                        return "\n".join(hits) + f"\n... (stopped at {MAX_SEARCH_HITS} matches; narrow the pattern)"
            files_hit += matched
        return "\n".join(hits) if hits else "No matches."

    @staticmethod
    def _clip(line: str) -> str:
        return line if len(line) <= MAX_LINE_CHARS else line[:MAX_LINE_CHARS] + " …"


_WS = re.compile(r"\s+")


def evidence_in_lines(evidence: str, lines: list[str], start: int, end: int, slack: int = 2) -> bool:
    """True if `evidence` appears (whitespace-insensitive) in lines start..end (1-based, ±slack).

    This is the hallucination guard: a finding must quote code that is really there.
    """
    want = _WS.sub(" ", evidence).strip()
    if len(want) < 4:
        return False
    lo = max(1, start - slack)
    hi = min(len(lines), end + slack)
    window = _WS.sub(" ", " ".join(lines[lo - 1 : hi])).strip()
    if want in window:
        return True
    # Allow evidence that elides the middle with "..." as long as every piece is present in order.
    pieces = [p.strip() for p in re.split(r"\.\.\.|…", want) if p.strip()]
    if len(pieces) < 2:
        return False
    pos = 0
    for piece in pieces:
        pos = window.find(piece, pos)
        if pos < 0:
            return False
        pos += len(piece)
    return True
