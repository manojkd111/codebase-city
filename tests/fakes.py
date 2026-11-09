"""A scripted stand-in for the Anthropic client, so agent code runs in tests without an API key."""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace
from typing import Any


def text(t: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=t)


def tool(name: str, args: dict[str, Any], id: str | None = None) -> SimpleNamespace:
    tool._n += 1
    return SimpleNamespace(type="tool_use", id=id or f"toolu_{tool._n}", name=name, input=args)


tool._n = 0


def response(*blocks: SimpleNamespace, stop: str | None = None) -> SimpleNamespace:
    if stop is None:
        stop = "tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn"
    return SimpleNamespace(
        content=list(blocks), stop_reason=stop, usage=SimpleNamespace(input_tokens=100, output_tokens=20)
    )


class FakeClient:
    """Routes each request to a script by a key derived from the request.

    `route(kwargs) -> key`; `scripts[key]` is a list of responses (or callables taking kwargs)
    consumed in order. Every request's kwargs are kept in `calls` for assertions.
    """

    def __init__(self, scripts: dict[str, list[Any]], route) -> None:
        self.scripts = {k: list(v) for k, v in scripts.items()}
        self.route = route
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> Any:
        with self._lock:
            # Snapshot: the caller keeps appending to the same messages list after the call.
            self.calls.append({**kwargs, "messages": list(kwargs.get("messages", []))})
            key = self.route(kwargs)
            script = self.scripts.get(key)
            if not script:
                return response(text("done"))
            item = script.pop(0)
        return item(kwargs) if callable(item) else item


def agent_route(kwargs: dict[str, Any]) -> str:
    """Coordinator requests carry a JSON output format; specialists are told their specialty."""
    if "format" in kwargs.get("output_config", {}):
        return "coordinator"
    system = kwargs.get("system", "")
    # Match the role prompt's own opening line only: the shared prompt names every specialty.
    for marker, role in (
        ("specialty is **security", "security"),
        ("specialty is **test gaps", "tests"),
        ("specialty is **bugs and technical debt", "debt"),
    ):
        if marker in system:
            return role
    return "other"


def plan_json(security: list[str], tests: list[str], debt: list[str]) -> SimpleNamespace:
    body = {
        "security": {"files": security, "reason": "s"},
        "tests": {"files": tests, "reason": "t"},
        "debt": {"files": debt, "reason": "d"},
    }
    return response(text(json.dumps(body)))
