"""Resolve import references to files in the repo, and roll edges up to roads."""

from __future__ import annotations

import posixpath
from collections import Counter

from .parse import ImportRef

JS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")


class PythonModuleIndex:
    """Maps dotted module names to repo files.

    A file's module root is the first ancestor directory that is *not* a package
    (has no __init__.py). That handles flat layouts, `src/` layouts and test folders.
    The full repo-relative dotted path is also indexed as a lower-priority alias.
    """

    def __init__(self, py_paths: list[str]):
        pkg_dirs = {posixpath.dirname(p) for p in py_paths if posixpath.basename(p) == "__init__.py"}
        self.by_name: dict[str, str] = {}
        self.package_of: dict[str, list[str]] = {}
        aliases: dict[str, str] = {}
        for path in sorted(py_paths, key=lambda p: (p.count("/"), p)):  # shallowest wins ties
            parts = path[:-3].split("/")
            is_init = parts[-1] == "__init__"
            if is_init:
                parts = parts[:-1]
            dir_parts = path.split("/")[:-1]
            i = len(dir_parts)
            while i > 0 and "/".join(dir_parts[:i]) in pkg_dirs:
                i -= 1
            module = parts[i:]
            if module:
                self.by_name.setdefault(".".join(module), path)
            if parts:
                aliases.setdefault(".".join(parts), path)
            self.package_of[path] = module if is_init else module[:-1]
        for name, path in aliases.items():
            self.by_name.setdefault(name, path)

    def longest(self, dotted: str) -> str | None:
        parts = dotted.split(".")
        for n in range(len(parts), 0, -1):
            hit = self.by_name.get(".".join(parts[:n]))
            if hit:
                return hit
        return None

    def resolve(self, src: str, ref: ImportRef) -> list[str]:
        if ref.level:
            pkg = self.package_of.get(src, [])
            up = ref.level - 1
            if up > len(pkg):
                return []
            base = pkg[: len(pkg) - up] if up else pkg
            target = [*base, *ref.module.split(".")] if ref.module else base
        else:
            target = ref.module.split(".") if ref.module else []
        if not target and not ref.names:
            return []
        out: list[str] = []
        prefix = ".".join(target)
        resolved_names = False
        for name in ref.names:  # `from pkg import submodule` imports the submodule file
            hit = self.by_name.get(f"{prefix}.{name}" if prefix else name)
            if hit:
                out.append(hit)
                resolved_names = True
        if not resolved_names and prefix:
            hit = self.longest(prefix) if not ref.level else self.by_name.get(prefix)
            if hit:
                out.append(hit)
        return out


def resolve_js(src: str, spec: str, files: set[str]) -> str | None:
    if not spec.startswith("."):
        return None  # bare specifier: an external package
    base = posixpath.normpath(posixpath.join(posixpath.dirname(src), spec))
    candidates = [base]
    stem, ext = posixpath.splitext(base)
    if ext in (".js", ".jsx", ".mjs", ".cjs"):  # TS ESM convention: "./a.js" means a.ts
        candidates += [stem + e for e in (".ts", ".tsx", ".mts", ".cts")]
    candidates += [base + e for e in JS_EXTS]
    candidates += [f"{base}/index{e}" for e in JS_EXTS]
    return next((c for c in candidates if c in files), None)


def is_external_js(spec: str) -> bool:
    return not spec.startswith(".") and not spec.startswith("/")


def build_edges(
    imports: dict[str, list[ImportRef]], languages: dict[str, str]
) -> tuple[set[tuple[str, str]], Counter[str]]:
    """Return (file->file edges, external package import counts)."""
    files = set(languages)
    py_index = PythonModuleIndex([p for p, lang in languages.items() if lang == "python"])
    edges: set[tuple[str, str]] = set()
    external: Counter[str] = Counter()
    for src, refs in imports.items():
        lang = languages[src]
        for ref in refs:
            if lang == "python":
                targets = py_index.resolve(src, ref)
                if not targets and not ref.level and ref.module:
                    external[ref.module.split(".")[0]] += 1
            else:
                hit = resolve_js(src, ref.module, files)
                targets = [hit] if hit else []
                if not hit and is_external_js(ref.module):
                    parts = ref.module.split("/")
                    external["/".join(parts[:2]) if ref.module.startswith("@") else parts[0]] += 1
            for dst in targets:
                if dst != src:
                    edges.add((src, dst))
    return edges, external


def roll_up_roads(edges: set[tuple[str, str]], district_of: dict[str, str]) -> list[tuple[str, str, int]]:
    """Aggregate file edges into district->district roads with import counts."""
    roads: Counter[tuple[str, str]] = Counter()
    for a, b in edges:
        da, db = district_of[a], district_of[b]
        if da != db:
            roads[(da, db)] += 1
    return [(a, b, n) for (a, b), n in sorted(roads.items())]
