"""Does Claude pick the right Codebase City MCP tool for a request? (Week 5)

    .venv/Scripts/python -m evals.tool_selection.run            # needs ANTHROPIC_API_KEY; ~15 short calls
    .venv/Scripts/python -m evals.tool_selection.run --list     # free: print the tools as Claude sees them

The tool definitions come straight from the MCP servers, so a vague docstring shows up here as a miss.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
SYSTEM = (
    "You are an assistant connected to Codebase City, which analyses GitHub repositories. "
    "Answer the user's request by calling the most appropriate tool."
)


async def _mcp_tools() -> list[Any]:
    from mcp_servers.findings.server import build as findings
    from mcp_servers.github.server import build as github
    from mcp_servers.repo_metrics.server import build as metrics

    tools = []
    for server in (metrics(), findings(publish=None), github(publish=None)):
        tools.extend(await server.list_tools())
    return tools


def to_claude_tools(mcp_tools: list[Any]) -> list[dict[str, Any]]:
    out = []
    for t in mcp_tools:
        schema = {k: v for k, v in t.input_schema.items() if k != "title"}
        schema["properties"] = {
            name: {k: v for k, v in prop.items() if k != "title"} for name, prop in schema.get("properties", {}).items()
        }
        out.append({"name": t.name, "description": t.description or "", "input_schema": schema})
    return sorted(out, key=lambda t: t["name"])  # stable order keeps the prompt cacheable


def grade(case: dict[str, Any], tool: str | None, args: dict[str, Any]) -> tuple[bool, bool]:
    """(right tool, right arguments). Arguments are only judged when the tool is the expected one."""
    ok_tool = tool == case["expected"] or tool in case.get("any_of", [])
    ok_args = tool == case["expected"] and all(args.get(k) == v for k, v in case.get("args", {}).items())
    return ok_tool, ok_args or (ok_tool and tool != case["expected"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--model", default=None)
    args = ap.parse_args(argv)

    tools = to_claude_tools(asyncio.run(_mcp_tools()))
    if args.list:
        for t in tools:
            print(f"{t['name']}: {t['description'].splitlines()[0]}")
        return 0

    from backend.agents import llm
    from backend.config import get_settings

    settings = get_settings()
    client = llm.make_client()
    cases = json.loads((HERE / "cases.json").read_text(encoding="utf-8"))["cases"]
    tool_hits = arg_hits = 0
    usage = llm.Usage()
    for case in cases:
        r = llm.create(
            client,
            model=args.model or settings.model,
            effort="low",
            max_tokens=4000,
            system=SYSTEM,
            tools=tools,
            messages=[{"role": "user", "content": case["prompt"]}],
        )
        usage.add(r)
        call = next((b for b in r.content if getattr(b, "type", None) == "tool_use"), None)
        name, got = (call.name, call.input) if call else (None, {})
        ok_tool, ok_args = grade(case, name, got)
        tool_hits += ok_tool
        arg_hits += ok_args
        mark = "ok  " if ok_tool and ok_args else ("args" if ok_tool else "MISS")
        print(f"[{mark}] {case['prompt'][:60]:<60} -> {name} {json.dumps(got)[:80]}")
    n = len(cases)
    print(
        f"\ntool accuracy: {tool_hits}/{n} = {tool_hits / n:.0%}   args accuracy: {arg_hits}/{n} = {arg_hits / n:.0%}"
    )
    print(f"tokens: {usage.input_tokens:,} in / {usage.output_tokens:,} out")
    return 0 if tool_hits == n else 1


if __name__ == "__main__":
    sys.exit(main())
