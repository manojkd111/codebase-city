"""MCP server: agent findings and human triage.

.venv/Scripts/python -m mcp_servers.findings
"""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.db.models import Finding, Repo
from backend.events.schema import FindingUpdated
from backend.ingest.runner import Publish, RunRecorder
from backend.views import finding_dict

from ..common import default_publisher, default_sessions

Category = Literal["security", "tests", "debt"]
Status = Literal["open", "confirmed", "rejected", "fixed"]


def build(sessions: sessionmaker[Session] | None = None, publish: Publish | None = None) -> MCPServer:
    sessions = sessions or default_sessions()
    publish = publish if publish is not None else default_publisher()
    mcp = MCPServer(
        "codebase-city-findings",
        instructions=(
            "Findings from Codebase City's review agents. Every finding's evidence was checked against the "
            "code when it was stored. Triage with set_finding_status; only confirmed findings can be fixed."
        ),
    )

    @mcp.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
    def list_findings(
        repo_id: int,
        status: Status | None = None,
        category: Category | None = None,
        file: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Findings for a repo, most severe first. Filter by status, category, or exact file path.
        Evidence and explanations are trimmed; use get_finding for the full text."""
        order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        with sessions() as s:
            if s.get(Repo, repo_id) is None:
                raise ToolError(f"Repo {repo_id} not found.")
            q = select(Finding).where(Finding.repo_id == repo_id)
            if status:
                q = q.where(Finding.status == status)
            if category:
                q = q.where(Finding.category == category)
            if file:
                q = q.where(Finding.file == file)
            rows = sorted(s.scalars(q), key=lambda f: (order.get(f.severity, 9), -f.confidence, f.id))
            out = []
            for f in rows[: max(1, min(limit, 200))]:
                d = finding_dict(f)
                d["evidence"] = d["evidence"][:300]
                d["explanation"] = d["explanation"][:300]
                d.pop("suggested_fix", None)
                out.append(d)
            return out

    @mcp.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
    def get_finding(finding_id: int) -> dict[str, Any]:
        """Full detail for one finding: evidence, explanation, suggested fix, confidence, status."""
        with sessions() as s:
            f = s.get(Finding, finding_id)
            if f is None:
                raise ToolError(f"Finding {finding_id} not found.")
            return finding_dict(f)

    @mcp.tool(
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False
        )
    )
    def set_finding_status(finding_id: int, status: Literal["open", "confirmed", "rejected"]) -> dict[str, Any]:
        """Human triage: confirm a real problem, reject a false positive, or reopen. Confirming is what
        allows a fix to be generated. 'fixed' is set only when a fix's pull request is opened."""
        with sessions() as s:
            f = s.get(Finding, finding_id)
            if f is None:
                raise ToolError(f"Finding {finding_id} not found.")
            if f.status == "fixed":
                raise ToolError("This finding already has a pull request; its status can't be changed here.")
            f.status = status
            s.commit()
            out = finding_dict(f)
        RunRecorder(out["repo_id"], sessions, publish)(FindingUpdated(finding_id=finding_id, status=status))
        return out

    return mcp
