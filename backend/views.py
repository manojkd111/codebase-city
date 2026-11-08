"""JSON views of DB rows, shared by the HTTP API and the MCP servers (so they never disagree)."""

from __future__ import annotations

from backend.db.models import AnalysisRun, File, Finding, Fix, Repo


def repo_dict(r: Repo) -> dict:
    return {
        "id": r.id,
        "source": r.source,
        "name": r.name,
        "commit_sha": r.commit_sha,
        "status": r.status,
        "error": r.error,
        "file_count": r.file_count,
        "stats": r.stats,
        "created_at": r.created_at.isoformat(),
    }


def file_dict(f: File) -> dict:
    return {
        c: getattr(f, c)
        for c in (
            "path",
            "district",
            "language",
            "size_bytes",
            "loc",
            "functions",
            "complexity_total",
            "complexity_max",
            "churn_30",
            "churn_90",
            "coverage",
            "risk",
            "parse_error",
            "is_test",
        )
    }


def finding_dict(f: Finding) -> dict:
    return {
        c: getattr(f, c)
        for c in (
            "id",
            "repo_id",
            "run_id",
            "agent_id",
            "category",
            "severity",
            "file",
            "line_start",
            "line_end",
            "title",
            "evidence",
            "explanation",
            "suggested_fix",
            "confidence",
            "needs_human_review",
            "status",
        )
    } | {"created_at": f.created_at.isoformat()}


def run_dict(r: AnalysisRun) -> dict:
    return {
        "id": r.id,
        "repo_id": r.repo_id,
        "status": r.status,
        "model": r.model,
        "error": r.error,
        "plan": r.plan,
        "input_tokens": r.input_tokens,
        "output_tokens": r.output_tokens,
        "created_at": r.created_at.isoformat(),
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
    }


def fix_dict(f: Fix) -> dict:
    return {
        c: getattr(f, c)
        for c in (
            "id",
            "finding_id",
            "repo_id",
            "status",
            "summary",
            "commit_message",
            "diff",
            "files_changed",
            "tests_passed",
            "test_output",
            "pr_url",
            "error",
            "input_tokens",
            "output_tokens",
        )
    } | {"created_at": f.created_at.isoformat()}
