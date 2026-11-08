"""tree-sitter parsing: function counts and import references.

Parsing is error tolerant: a file with syntax errors still yields whatever imports
tree-sitter could recover, and is flagged with `has_error`.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from tree_sitter import Language, Node, Parser


@dataclass(frozen=True)
class ImportRef:
    """One import statement.

    Python: `module` is the dotted name, `level` the number of leading dots,
    `names` the imported names (`from m import a, b`).
    JS/TS: `module` is the raw specifier ("./lib/a", "react"), level 0, names ().
    """

    module: str
    level: int = 0
    names: tuple[str, ...] = ()


@dataclass
class ParsedFile:
    functions: int = 0
    imports: list[ImportRef] = field(default_factory=list)
    has_error: bool = False


_local = threading.local()  # Parser objects are not shared across threads


def _load_language(grammar: str) -> Language:
    if grammar == "python":
        import tree_sitter_python as m

        return Language(m.language())
    if grammar == "javascript":
        import tree_sitter_javascript as m

        return Language(m.language())
    if grammar == "typescript":
        import tree_sitter_typescript as m

        return Language(m.language_typescript())
    if grammar == "tsx":
        import tree_sitter_typescript as m

        return Language(m.language_tsx())
    raise ValueError(f"no grammar named {grammar!r}")


def _parser(grammar: str) -> Parser:
    cache: dict[str, Parser] = getattr(_local, "parsers", None) or {}
    _local.parsers = cache
    if grammar not in cache:
        cache[grammar] = Parser(_load_language(grammar))
    return cache[grammar]


def _text(node: Node | None) -> str:
    return node.text.decode("utf-8", "replace") if node is not None and node.text else ""


def _dotted(node: Node) -> str:
    return _text(node.child_by_field_name("name") if node.type == "aliased_import" else node)


def _python(root: Node, out: ParsedFile) -> None:
    stack = [root]
    while stack:  # explicit stack: deeply nested code must not hit the recursion limit
        n = stack.pop()
        t = n.type
        if t == "function_definition":
            out.functions += 1
        elif t == "import_statement":
            for c in n.children_by_field_name("name"):
                out.imports.append(ImportRef(_dotted(c)))
            continue
        elif t == "import_from_statement":
            mod = n.child_by_field_name("module_name")
            level, module = 0, ""
            if mod is not None and mod.type == "relative_import":
                for c in mod.children:
                    if c.type == "import_prefix":
                        level = _text(c).count(".")
                    elif c.type == "dotted_name":
                        module = _text(c)
            else:
                module = _text(mod)
            names = tuple(_dotted(c) for c in n.children_by_field_name("name"))
            out.imports.append(ImportRef(module, level, names))
            continue
        stack.extend(reversed(n.children))  # reversed: pop() then visits in source order


_JS_FUNCTIONS = frozenset(
    {
        "function_declaration",
        "function_expression",
        "function",
        "arrow_function",
        "method_definition",
        "generator_function_declaration",
        "generator_function",
    }
)


def _string_literal(node: Node | None) -> str | None:
    if node is None or node.type not in ("string", "template_string"):
        return None
    raw = _text(node)
    if "${" in raw:  # dynamic specifier; cannot resolve statically
        return None
    return raw[1:-1] if len(raw) >= 2 else None


def _javascript(root: Node, out: ParsedFile) -> None:
    stack = [root]
    while stack:
        n = stack.pop()
        t = n.type
        if t in _JS_FUNCTIONS:
            out.functions += 1
        if t in ("import_statement", "export_statement"):
            spec = _string_literal(n.child_by_field_name("source"))
            if spec:
                out.imports.append(ImportRef(spec))
        elif t == "call_expression":
            fn = n.child_by_field_name("function")
            if fn is not None and (fn.type == "import" or (fn.type == "identifier" and _text(fn) == "require")):
                args = n.child_by_field_name("arguments")
                if args is not None and args.named_child_count:
                    spec = _string_literal(args.named_children[0])
                    if spec:
                        out.imports.append(ImportRef(spec))
        stack.extend(reversed(n.children))  # reversed: pop() then visits in source order


def parse_source(code: bytes, grammar: str) -> ParsedFile:
    tree = _parser(grammar).parse(code)
    out = ParsedFile(has_error=tree.root_node.has_error)
    if grammar == "python":
        _python(tree.root_node, out)
    else:
        _javascript(tree.root_node, out)
    return out
