from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from backend.db.models import Finding, Fix
from mcp_servers.findings.server import build as build_findings
from mcp_servers.github.server import build as build_github
from mcp_servers.repo_metrics.server import build as build_metrics

from .test_agents import GOOD, _setup


def unwrap(result):
    """Lists come back as one content block per item, with the whole value in structured_content."""
    sc = result.structured_content
    if sc is not None:
        return sc["result"] if set(sc) == {"result"} else sc
    return json.loads(result.content[0].text) if result.content else None


def call(server, name, args=None):
    result = asyncio.run(server.call_tool(name, args or {}))
    assert not result.is_error, result
    return unwrap(result)


@pytest.fixture
def city(tmp_path, fixture_repo):
    settings, sessions, repo_id, _ = _setup(tmp_path, fixture_repo)
    return settings, sessions, repo_id


def test_metrics_server(city):
    _, sessions, repo_id = city
    mcp = build_metrics(sessions)
    tools = {t.name: t for t in asyncio.run(mcp.list_tools())}
    assert set(tools) == {"list_repos", "get_hotspots", "get_file_metrics", "get_import_neighbors", "get_city_summary"}
    assert all(t.annotations.read_only_hint for t in tools.values())

    assert call(mcp, "list_repos")[0]["id"] == repo_id
    hot = call(mcp, "get_hotspots", {"repo_id": repo_id, "limit": 3})
    assert hot[0]["path"] == "pkg/core.py" and not any(h["is_test"] for h in hot)
    assert call(mcp, "get_file_metrics", {"repo_id": repo_id, "path": "pkg/core.py"})["open_findings"] == 0
    assert call(mcp, "get_import_neighbors", {"repo_id": repo_id, "path": "pkg/util.py"})["imported_by"] == [
        "pkg/core.py",
        "pkg/sub/deep.py",
    ]
    assert call(mcp, "get_city_summary", {"repo_id": repo_id})["files"] > 5
    with pytest.raises(ToolError, match="not found"):
        call(mcp, "get_hotspots", {"repo_id": 999})
    with pytest.raises(ToolError, match="No file"):
        call(mcp, "get_file_metrics", {"repo_id": repo_id, "path": "nope.py"})


def test_findings_server_triage_records_events(city):
    _, sessions, repo_id = city
    with sessions() as s:
        f = Finding(repo_id=repo_id, **GOOD, needs_human_review=False)
        s.add(f)
        s.commit()
        fid = f.id
    published = []
    mcp = build_findings(sessions, publish=published.append)
    listed = call(mcp, "list_findings", {"repo_id": repo_id, "category": "debt"})
    assert [x["id"] for x in listed] == [fid] and "suggested_fix" not in listed[0]
    assert call(mcp, "get_finding", {"finding_id": fid})["suggested_fix"] == GOOD["suggested_fix"]
    assert call(mcp, "set_finding_status", {"finding_id": fid, "status": "confirmed"})["status"] == "confirmed"
    assert published[-1].type == "finding.updated"
    with pytest.raises(ToolError, match="Input should be 'open', 'confirmed' or 'rejected'"):
        call(mcp, "set_finding_status", {"finding_id": fid, "status": "fixed"})  # never settable by hand


def test_github_server_gates_pull_requests(city):
    settings, sessions, repo_id = city
    with sessions() as s:
        f = Finding(repo_id=repo_id, **GOOD, needs_human_review=False, status="open")
        s.add(f)
        s.commit()
        fix = Fix(finding_id=f.id, repo_id=repo_id, status="ready", diff="d", summary="s")
        s.add(fix)
        s.commit()
        fid, fix_id = f.id, fix.id

    no_token = build_github(sessions, replace(settings, github_token=None), publish=None)
    with pytest.raises(ToolError, match="GITHUB_TOKEN"):
        call(no_token, "open_pull_request", {"fix_id": fix_id})
    mcp = build_github(sessions, replace(settings, github_token="t"), publish=None)
    tool = {t.name: t for t in asyncio.run(mcp.list_tools())}["open_pull_request"]
    assert tool.annotations.open_world_hint and not tool.annotations.read_only_hint
    with pytest.raises(ToolError, match="confirmed by a human"):
        call(mcp, "open_pull_request", {"fix_id": fix_id})
    assert call(mcp, "get_fix", {"fix_id": fix_id})["diff"] == "d"
    assert call(mcp, "list_fixes", {"finding_id": fid})[0]["id"] == fix_id


def test_metrics_server_speaks_mcp_over_stdio(city, tmp_path):
    """The real thing Claude Code launches: a subprocess speaking MCP on stdin/stdout."""
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    settings, _, repo_id = city
    root = Path(__file__).resolve().parents[1]
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_servers.repo_metrics"],
        env={**os.environ, "DATABASE_URL": settings.database_url},
        cwd=str(root),
    )

    async def go():
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            hot = await session.call_tool("get_hotspots", {"repo_id": repo_id, "limit": 1})
            return [t.name for t in tools.tools], unwrap(hot)

    names, hot = asyncio.run(asyncio.wait_for(go(), 60))
    assert "get_hotspots" in names and hot[0]["path"] == "pkg/core.py"
