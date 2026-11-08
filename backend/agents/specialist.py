"""One specialist agent: a tool loop over its assigned files.

We own the loop (rather than the SDK tool runner) because every tool call becomes an event:
the city's drones fly to whatever file the agent is reading.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from backend.events.schema import AgentMoved, AgentToolCall, AgentTurn, Payload

from . import llm
from .tools import ToolBox, specialist_tools

CUT_OFF = "Your previous response was cut off before this tool call was complete. Send it again."


@dataclass
class SpecialistResult:
    agent_id: str
    role: str
    finding_ids: list[int] = field(default_factory=list)
    rejected: int = 0
    turns: int = 0
    summary: str = ""
    usage: llm.Usage = field(default_factory=llm.Usage)
    error: str | None = None


def run_specialist(
    client: Any,
    *,
    model: str,
    effort: str,
    max_turns: int,
    run_id: int,
    agent_id: str,
    role: str,
    files: list[str],
    repo_name: str,
    commit: str,
    toolbox: ToolBox,
    emit: Callable[[Payload], Any],
) -> SpecialistResult:
    result = SpecialistResult(agent_id=agent_id, role=role)
    system = (
        llm.prompt(
            "specialist_common",
            repo=repo_name,
            commit=commit[:12] or "unknown",
            files="\n".join(f"- {f}" for f in files),
        )
        + "\n\n"
        + llm.prompt(role)
    )
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": f"Begin your review. You have up to {max_turns} turns of tool calls."}
    ]
    tools = specialist_tools(role)
    last_path: str | None = None

    try:
        for turn in range(1, max_turns + 1):
            result.turns = turn
            t0 = time.monotonic()
            response = llm.create(client, model=model, effort=effort, system=system, tools=tools, messages=messages)
            result.usage.add(response)
            n_calls = sum(1 for b in response.content if getattr(b, "type", None) == "tool_use")
            emit(
                AgentTurn(
                    run_id=run_id,
                    agent_id=agent_id,
                    turn=turn,
                    max_turns=max_turns,
                    input_tokens=result.usage.input_tokens,
                    output_tokens=result.usage.output_tokens,
                    tool_calls=n_calls,
                    duration_ms=int((time.monotonic() - t0) * 1000),
                )
            )
            # Append the full content (thinking blocks included) unchanged: the history stays append-only.
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "refusal":
                result.error = "The model declined to continue this review."
                break
            if response.stop_reason == "pause_turn":
                continue

            calls = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
            if not calls:
                result.summary = llm.text_of(response).strip()
                break

            outputs = []
            for call in calls:
                if response.stop_reason == "max_tokens":
                    outputs.append(_result(call.id, CUT_OFF, True))
                    continue
                args = call.input if isinstance(call.input, dict) else {}
                t1 = time.monotonic()
                r = toolbox.run(call.name, args)
                emit(
                    AgentToolCall(
                        run_id=run_id,
                        agent_id=agent_id,
                        tool=call.name,
                        target=_target(call.name, args),
                        ok=not r.is_error,
                        duration_ms=int((time.monotonic() - t1) * 1000),
                        note=r.content[:160] if r.is_error else "",
                    )
                )
                if r.moved_to and r.moved_to != last_path:
                    last_path = r.moved_to
                    emit(AgentMoved(run_id=run_id, agent_id=agent_id, path=r.moved_to, action=r.action or "read"))
                outputs.append(_result(call.id, r.content, r.is_error))
            # All results for one turn go back in a single user message.
            messages.append({"role": "user", "content": outputs})
        else:
            result.summary = f"Stopped after the {max_turns}-turn budget."
    except llm.LLMUnavailable:
        raise
    except Exception as exc:  # one agent failing must not take down the others
        result.error = f"{type(exc).__name__}: {exc}"[:500]

    result.finding_ids = list(toolbox.accepted)
    result.rejected = toolbox.rejected
    return result


def _target(tool: str, args: dict[str, Any]) -> str:
    """A short human label for what a tool call was about (shown in the activity feed)."""
    if tool == "read_file":
        start, end = args.get("start_line", 1), args.get("end_line", 0)
        return f"{args.get('path', '?')}:{start}-{end or 'end'}"
    if tool == "search_code":
        return f"/{args.get('pattern', '')}/ in {args.get('path_glob', '*')}"
    if tool == "submit_finding":
        return f"{args.get('file', '?')}:{args.get('line_start', '?')} {args.get('title', '')}"[:200]
    return str(args.get("path", ""))[:200]


def _result(tool_use_id: str, content: str, is_error: bool) -> dict[str, Any]:
    out: dict[str, Any] = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
    if is_error:
        out["is_error"] = True
    return out
