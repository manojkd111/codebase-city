"""The fix agent: edits a throwaway checkout to resolve one finding, tests in the sandbox."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from backend.agents import llm
from backend.agents.tools import READ_FILE, SEARCH_CODE, _strict
from backend.agents.workspace import Workspace, WorkspaceError

from .sandbox import DockerSandbox, SandboxResult, SandboxUnavailable, TestPlan

MAX_FILE_BYTES = 512 * 1024

EDIT_FILE = _strict(
    "edit_file",
    "Replace one exact occurrence of old_text with new_text in an existing file. old_text must match "
    "exactly once (include enough surrounding lines to be unique). Read the file first.",
    {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}},
)
CREATE_FILE = _strict(
    "create_file",
    "Create a new file (for example a test). Fails if the file already exists; use edit_file for that.",
    {"path": {"type": "string"}, "content": {"type": "string"}},
)
RUN_TESTS = _strict(
    "run_tests",
    "Run the repository's test suite in an isolated sandbox (no network) and return the tail of the output.",
    {},
)
FINISH = _strict(
    "finish",
    "Call once when done. summary explains the change for a pull request; commit_message is one line.",
    {"summary": {"type": "string"}, "commit_message": {"type": "string"}},
)
FIXER_TOOLS = [READ_FILE, SEARCH_CODE, EDIT_FILE, CREATE_FILE, RUN_TESTS, FINISH]


class EditableWorkspace(Workspace):
    """A Workspace that can also change files, inside the root, never in .git, never via symlinks."""

    def _check_writable(self, rel: str) -> None:
        parts = PurePosixPath(rel).parts
        if parts and parts[0] in (".git", ".cbc-venv"):
            raise WorkspaceError(f"Refusing to write inside {parts[0]}/")

    def edit(self, path: str, old: str, new: str) -> str:
        rel, full = self._resolve(path)
        self._check_writable(rel)
        text = full.read_bytes().decode("utf-8")
        if "\r\n" in text:
            # The model sees lines without \r, and Windows checkouts often use CRLF: match the file.
            old = old.replace("\r\n", "\n").replace("\n", "\r\n")
            new = new.replace("\r\n", "\n").replace("\n", "\r\n")
        count = text.count(old) if old else 0
        if count != 1:
            raise WorkspaceError(
                f"old_text matched {count} times in {rel}; it must match exactly once"
                + (" (read the file and copy the text exactly)" if count == 0 else " (include more context)")
            )
        updated = text.replace(old, new, 1)
        if len(updated.encode()) > MAX_FILE_BYTES:
            raise WorkspaceError("Resulting file is too large.")
        full.write_bytes(updated.encode("utf-8"))
        self._cache.pop(rel, None)
        line = text[: text.index(old)].count("\n") + 1
        return f"Edited {rel} at line {line}."

    def create(self, path: str, content: str) -> str:
        rel = self.normalize(path)
        self._check_writable(rel)
        full = self.root / rel
        if rel in self._known or full.exists():
            raise WorkspaceError(f"{rel} already exists; use edit_file.")
        if len(content.encode()) > MAX_FILE_BYTES:
            raise WorkspaceError("File is too large.")
        # Every existing ancestor must be a real directory inside the root (no symlink hops).
        cur = self.root
        for part in PurePosixPath(rel).parts[:-1]:
            cur = cur / part
            if cur.is_symlink():
                raise WorkspaceError(f"Refusing to write through symlink: {rel!r}")
        if not full.parent.resolve().is_relative_to(self.root):
            raise WorkspaceError(f"Path escapes the repository: {rel!r}")
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(content.encode("utf-8"))
        self.files.append(rel)
        self._known.add(rel)
        return f"Created {rel}."


@dataclass
class FixOutcome:
    summary: str = ""
    commit_message: str = ""
    edits: int = 0
    turns: int = 0
    last_test: SandboxResult | None = None
    sandbox_note: str | None = None
    usage: llm.Usage = field(default_factory=llm.Usage)
    error: str | None = None


def run_fix_agent(
    client: Any,
    *,
    model: str,
    effort: str,
    max_turns: int,
    finding: dict[str, Any],
    repo_name: str,
    workspace: EditableWorkspace,
    sandbox: DockerSandbox | None,
    plan: TestPlan | None,
    progress: Callable[[str, str], None],
) -> FixOutcome:
    out = FixOutcome()
    system = llm.prompt("fixer", repo=repo_name, **{k: str(v) for k, v in finding.items()})
    can_test = sandbox is not None and plan is not None and sandbox.available()
    tools = FIXER_TOOLS if can_test else [t for t in FIXER_TOOLS if t["name"] != "run_tests"]
    if not can_test:
        if sandbox is None:
            reason = "the test sandbox is turned off on this server."
        elif plan is None:
            reason = "no supported test setup detected."
        else:
            reason = "Docker is not available."
        out.sandbox_note = "Tests could not be run: " + reason
    intro = "Fix the problem described above."
    if out.sandbox_note:
        intro += f" Note: {out.sandbox_note} Be careful and keep the change minimal."
    messages: list[dict[str, Any]] = [{"role": "user", "content": intro}]
    setup_done = False

    for turn in range(1, max_turns + 1):
        out.turns = turn
        response = llm.create(client, model=model, effort=effort, system=system, tools=tools, messages=messages)
        out.usage.add(response)
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason == "refusal":
            out.error = "The model declined to make this fix."
            return out
        if response.stop_reason == "pause_turn":
            continue
        calls = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
        if not calls:
            out.summary = out.summary or llm.text_of(response).strip()
            return out

        results = []
        finished = False
        for call in calls:
            args = call.input if isinstance(call.input, dict) else {}
            try:
                if response.stop_reason == "max_tokens":
                    raise WorkspaceError("Your response was cut off before this call was complete; send it again.")
                if call.name == "read_file":
                    rel = workspace.normalize(args["path"])
                    content = workspace.read(rel, args.get("start_line", 1), args.get("end_line", 0))
                    progress("read", rel)
                elif call.name == "search_code":
                    content = workspace.search(args["pattern"], args.get("path_glob", "*"))
                elif call.name == "edit_file":
                    content = workspace.edit(args["path"], args["old_text"], args["new_text"])
                    out.edits += 1
                    progress("edit", workspace.normalize(args["path"]))
                elif call.name == "create_file":
                    content = workspace.create(args["path"], args["content"])
                    out.edits += 1
                    progress("edit", workspace.normalize(args["path"]))
                elif call.name == "run_tests" and can_test:
                    progress("test", "running tests in the sandbox")
                    out.last_test = sandbox.run_tests(workspace.root, plan, setup=not setup_done)
                    setup_done = setup_done or "[setup failed]" not in out.last_test.output
                    status = "PASSED" if out.last_test.passed else f"FAILED (exit {out.last_test.exit_code})"
                    content = f"Tests {status}.\n{out.last_test.output[-6000:]}"
                elif call.name == "finish":
                    out.summary = str(args.get("summary", "")).strip()
                    lines = str(args.get("commit_message", "")).strip().splitlines()
                    out.commit_message = lines[0][:72] if lines else ""
                    content = "Recorded."
                    finished = True
                else:
                    raise WorkspaceError(f"Unknown tool: {call.name}")
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": content})
            except (WorkspaceError, KeyError, SandboxUnavailable, UnicodeDecodeError) as exc:
                msg = f"Missing argument: {exc}" if isinstance(exc, KeyError) else str(exc)
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": msg, "is_error": True})
        messages.append({"role": "user", "content": results})
        if finished:
            return out
    out.error = out.error or f"Stopped after the {max_turns}-turn budget without calling finish."
    return out


def git_files(root: Path) -> list[str]:
    from backend.ingest.source import _git

    r = _git(["ls-files", "-z"], cwd=root)
    return [p for p in r.stdout.split("\0") if p] if r.returncode == 0 else []
