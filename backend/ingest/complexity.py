"""Per-file complexity via lizard (works across all languages in languages.py)."""

from __future__ import annotations

from dataclasses import dataclass

from lizard import analyze_file


@dataclass(frozen=True)
class Complexity:
    nloc: int  # lines of code, excluding blanks and comments
    functions: int
    total: int  # sum of cyclomatic complexity over all functions
    max: int  # most complex single function


def measure(filename: str, code: str) -> Complexity:
    info = analyze_file.analyze_source_code(filename, code)
    ccns = [f.cyclomatic_complexity for f in info.function_list]
    return Complexity(
        nloc=info.nloc,
        functions=len(ccns),
        total=sum(ccns),
        max=max(ccns, default=0),
    )
