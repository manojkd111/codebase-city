"""MCP server: deterministic repo metrics (read-only). Nothing here calls a model.

.venv/Scripts/python -m mcp_servers.repo_metrics
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.db.models import File, Finding, ImportEdge, Repo, Road
from backend.views import file_dict, repo_dict

from ..common import default_sessions

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)


def build(sessions: sessionmaker[Session] | None = None) -> MCPServer:
    sessions = sessions or default_sessions()
    mcp = MCPServer(
        "codebase-city-metrics",
        instructions=(
            "Deterministic metrics for repositories ingested into Codebase City: hotspots ranked by "
            "risk (complexity x churn x coverage), per-file metrics, and the import graph. "
            "Use list_repos first to get a repo_id."
        ),
    )

    def _repo(s: Session, repo_id: int) -> Repo:
        repo = s.get(Repo, repo_id)
        if repo is None:
            raise ToolError(f"Repo {repo_id} not found. Call list_repos to see ingested repos.")
        return repo

    @mcp.tool(annotations=READ_ONLY)
    def list_repos() -> list[dict[str, Any]]:
        """List ingested repositories (newest first) with their id, status, and file count."""
        with sessions() as s:
            return [
                {k: v for k, v in repo_dict(r).items() if k != "stats"}
                for r in s.scalars(select(Repo).order_by(Repo.id.desc()).limit(50))
            ]

    @mcp.tool(annotations=READ_ONLY)
    def get_hotspots(
        repo_id: int, limit: int = 10, district: str | None = None, include_tests: bool = False
    ) -> list[dict[str, Any]]:
        """Riskiest files in a repo, highest risk first. Test files are excluded unless include_tests."""
        limit = max(1, min(limit, 100))
        with sessions() as s:
            _repo(s, repo_id)
            q = select(File).where(File.repo_id == repo_id)
            if not include_tests:
                q = q.where(File.is_test.is_(False))
            if district:
                q = q.where(File.district == district)
            return [file_dict(f) for f in s.scalars(q.order_by(File.risk.desc(), File.path).limit(limit))]

    @mcp.tool(annotations=READ_ONLY)
    def get_file_metrics(repo_id: int, path: str) -> dict[str, Any]:
        """Metrics for one file: LOC, functions, complexity, churn, coverage, risk, and open findings."""
        with sessions() as s:
            _repo(s, repo_id)
            f = s.scalar(select(File).where(File.repo_id == repo_id, File.path == path))
            if f is None:
                raise ToolError(f"No file {path!r} in repo {repo_id}. Paths are repo-relative, e.g. src/app.py.")
            open_findings = s.scalar(
                select(func.count()).where(
                    Finding.repo_id == repo_id, Finding.file == path, Finding.status.in_(("open", "confirmed"))
                )
            )
            return file_dict(f) | {"open_findings": open_findings}

    @mcp.tool(annotations=READ_ONLY)
    def get_import_neighbors(repo_id: int, path: str) -> dict[str, list[str]]:
        """Files this file imports, and files that import it (within the repo)."""
        with sessions() as s:
            _repo(s, repo_id)
            out = [
                e.dst_path
                for e in s.scalars(select(ImportEdge).where(ImportEdge.repo_id == repo_id, ImportEdge.src_path == path))
            ]
            inc = [
                e.src_path
                for e in s.scalars(select(ImportEdge).where(ImportEdge.repo_id == repo_id, ImportEdge.dst_path == path))
            ]
            return {"imports": sorted(out), "imported_by": sorted(inc)}

    @mcp.tool(annotations=READ_ONLY)
    def get_city_summary(repo_id: int) -> dict[str, Any]:
        """One-screen overview: districts with file counts, cross-district import roads, and totals."""
        with sessions() as s:
            repo = _repo(s, repo_id)
            files = s.scalars(select(File).where(File.repo_id == repo_id)).all()
            by_district: dict[str, dict[str, Any]] = {}
            for f in files:
                d = by_district.setdefault(f.district, {"files": 0, "loc": 0, "max_risk": 0.0})
                d["files"] += 1
                d["loc"] += f.loc
                d["max_risk"] = max(d["max_risk"], round(f.risk, 2))
            roads = [
                {"from": r.src, "to": r.dst, "imports": r.imports}
                for r in s.scalars(select(Road).where(Road.repo_id == repo_id))
            ]
            return {
                "repo": repo.name or repo.source,
                "commit_sha": repo.commit_sha,
                "files": len(files),
                "loc": sum(f.loc for f in files),
                "districts": by_district,
                "roads": roads,
            }

    return mcp
