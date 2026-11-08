"""Which files become buildings, and how each one is parsed."""

from __future__ import annotations

# extension -> (language name, tree-sitter grammar or None)
# Languages with a grammar get import extraction (roads); every language here gets
# lizard complexity, so the city still shows them as buildings.
EXTENSIONS: dict[str, tuple[str, str | None]] = {
    ".py": ("python", "python"),
    ".js": ("javascript", "javascript"),
    ".jsx": ("javascript", "javascript"),
    ".mjs": ("javascript", "javascript"),
    ".cjs": ("javascript", "javascript"),
    ".ts": ("typescript", "typescript"),
    ".mts": ("typescript", "typescript"),
    ".cts": ("typescript", "typescript"),
    ".tsx": ("typescript", "tsx"),
    ".go": ("go", None),
    ".java": ("java", None),
    ".rb": ("ruby", None),
    ".rs": ("rust", None),
    ".c": ("c", None),
    ".h": ("c", None),
    ".cc": ("cpp", None),
    ".cpp": ("cpp", None),
    ".hpp": ("cpp", None),
    ".cs": ("csharp", None),
    ".php": ("php", None),
    ".swift": ("swift", None),
    ".kt": ("kotlin", None),
    ".scala": ("scala", None),
    ".lua": ("lua", None),
}

# Directories that are never part of "the codebase" for city purposes.
SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "bower_components",
        "vendor",
        "third_party",
        ".venv",
        "venv",
        "env",
        ".env",
        ".tox",
        ".nox",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "site-packages",
        "dist",
        "build",
        "out",
        "target",
        ".next",
        ".nuxt",
        ".cache",
        "coverage",
        "htmlcov",
        ".idea",
        ".vscode",
    }
)

# Generated/minified files would become misleading skyscrapers.
SKIP_SUFFIXES: tuple[str, ...] = (".min.js", ".bundle.js", ".d.ts", "_pb2.py", ".pb.go")


def language_for(filename: str) -> tuple[str, str | None] | None:
    lower = filename.lower()
    if lower.endswith(SKIP_SUFFIXES):
        return None
    dot = lower.rfind(".")
    if dot == -1:
        return None
    return EXTENSIONS.get(lower[dot:])


_TEST_DIRS = frozenset({"test", "tests", "__tests__", "spec", "specs", "testing"})


def is_test_path(path: str) -> bool:
    """Heuristic: is this repo-relative path a test file? (pytest, jest, mocha, go conventions)"""
    parts = path.lower().split("/")
    name = parts[-1]
    if any(p in _TEST_DIRS for p in parts[:-1]):
        return True
    stem = name.rsplit(".", 1)[0]
    return (
        stem.startswith("test_") or stem.endswith("_test") or stem == "conftest" or ".test." in name or ".spec." in name
    )
