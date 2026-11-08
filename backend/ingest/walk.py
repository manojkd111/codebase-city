"""Walk a checkout and list the source files that become buildings."""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .languages import SKIP_DIRS, language_for


@dataclass(frozen=True)
class SourceFile:
    path: str  # repo-relative, forward slashes
    abs_path: Path
    size_bytes: int
    language: str
    grammar: str | None


@dataclass
class WalkResult:
    files: list[SourceFile]
    skipped: Counter = field(default_factory=Counter)
    truncated: bool = False


def walk_repo(root: Path, *, max_files: int, max_file_bytes: int) -> WalkResult:
    result = WalkResult(files=[])
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        # Prune in place; sort for a deterministic order (stable cities across runs).
        dirnames[:] = sorted(
            d
            for d in dirnames
            if d not in SKIP_DIRS and not d.endswith(".egg-info") and not (Path(dirpath) / d).is_symlink()
        )
        for name in sorted(filenames):
            lang = language_for(name)
            if lang is None:
                continue
            abs_path = Path(dirpath) / name
            if abs_path.is_symlink():  # could point outside the clone
                result.skipped["symlink"] += 1
                continue
            size = abs_path.stat().st_size
            if size > max_file_bytes:
                result.skipped["too_large"] += 1
                continue
            if len(result.files) >= max_files:
                result.truncated = True
                result.skipped["over_file_cap"] += 1
                continue
            rel = abs_path.relative_to(root).as_posix()
            result.files.append(SourceFile(rel, abs_path, size, lang[0], lang[1]))
    return result
