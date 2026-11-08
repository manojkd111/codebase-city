"""Group files into districts (top-level modules).

If one directory holds nearly all the code (a `src/` layout, or `src/pkg/`), we descend
into it so districts are the meaningful packages, not one giant plate.
"""

from __future__ import annotations

import posixpath
from collections import Counter

ROOT_DISTRICT = "(root)"
DOMINANCE = 0.8
MAX_DESCENT = 3


def common_prefix(paths: list[str]) -> list[str]:
    prefix: list[str] = []
    for _ in range(MAX_DESCENT):
        depth = len(prefix)
        inside = [p.split("/") for p in paths if p.split("/")[:depth] == prefix]
        # Only files *below* a directory at this level can make it dominant.
        dirs = Counter(parts[depth] for parts in inside if len(parts) > depth + 1)
        if not dirs:
            break
        top, count = dirs.most_common(1)[0]
        if count / max(len(paths), 1) < DOMINANCE:
            break
        prefix.append(top)
    return prefix


def assign_districts(paths: list[str]) -> dict[str, str]:
    prefix = common_prefix(paths)
    depth = len(prefix)
    out: dict[str, str] = {}
    for p in paths:
        parts = p.split("/")
        if parts[:depth] != prefix:
            out[p] = ROOT_DISTRICT  # outside the dominant folder, e.g. setup.py next to src/
        elif len(parts) > depth + 1:
            out[p] = "/".join(parts[: depth + 1])  # inside a sub-folder: that's the district
        else:
            out[p] = "/".join(prefix) or ROOT_DISTRICT  # loose file at the prefix level
    return _collapse_single_chains(out)


def _collapse_single_chains(district_of: dict[str, str]) -> dict[str, str]:
    """Rename `src` -> `src/flask` when every file in the district sits under one sub-folder."""
    members: dict[str, list[str]] = {}
    for path, d in district_of.items():
        members.setdefault(d, []).append(path)
    rename: dict[str, str] = {}
    for d, paths in members.items():
        if d == ROOT_DISTRICT:
            continue
        dirs = [posixpath.dirname(p) for p in paths]
        common = posixpath.commonpath(dirs) if dirs else d
        if common != d and common.startswith(d + "/") and common not in members:
            rename[d] = common
    return {p: rename.get(d, d) for p, d in district_of.items()}
