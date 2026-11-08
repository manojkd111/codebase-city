"""SQLAlchemy models. Works on SQLite (local dev) and Postgres (deploy)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Repo(Base):
    __tablename__ = "repos"
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(500))
    name: Mapped[str | None] = mapped_column(String(300))
    commit_sha: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|running|ready|failed
    error: Mapped[str | None] = mapped_column(Text)
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


class District(Base):
    __tablename__ = "districts"
    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(500))
    file_count: Mapped[int] = mapped_column(Integer)
    total_bytes: Mapped[int] = mapped_column(Integer)


class File(Base):
    __tablename__ = "files"
    __table_args__ = (UniqueConstraint("repo_id", "path"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(String(1000))
    district: Mapped[str] = mapped_column(String(500))
    language: Mapped[str] = mapped_column(String(30))
    size_bytes: Mapped[int] = mapped_column(Integer)
    loc: Mapped[int] = mapped_column(Integer)
    functions: Mapped[int] = mapped_column(Integer)
    complexity_total: Mapped[int] = mapped_column(Integer)
    complexity_max: Mapped[int] = mapped_column(Integer)
    churn_30: Mapped[int] = mapped_column(Integer)
    churn_90: Mapped[int] = mapped_column(Integer)
    coverage: Mapped[float | None] = mapped_column(Float)
    risk: Mapped[float] = mapped_column(Float, index=True)
    parse_error: Mapped[bool] = mapped_column(default=False)
    is_test: Mapped[bool] = mapped_column(default=False, index=True)


class ImportEdge(Base):
    __tablename__ = "import_edges"
    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id", ondelete="CASCADE"), index=True)
    src_path: Mapped[str] = mapped_column(String(1000))
    dst_path: Mapped[str] = mapped_column(String(1000))


class Road(Base):
    __tablename__ = "roads"
    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id", ondelete="CASCADE"), index=True)
    src: Mapped[str] = mapped_column(String(500))
    dst: Mapped[str] = mapped_column(String(500))
    imports: Mapped[int] = mapped_column(Integer)


class EventRow(Base):
    """Every event ever emitted, so any run can be replayed exactly."""

    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("repo_id", "seq"), Index("ix_events_repo_seq", "repo_id", "seq"))
    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(50))
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    ts: Mapped[datetime] = mapped_column(default=_now)


class Finding(Base):
    """Agent findings (Week 3). Mirrors the submit_finding tool schema."""

    __tablename__ = "findings"
    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("analysis_runs.id", ondelete="SET NULL"), index=True)
    agent_id: Mapped[str] = mapped_column(String(50), default="")
    category: Mapped[str] = mapped_column(String(20))  # security | tests | debt
    severity: Mapped[str] = mapped_column(String(20))  # critical | high | medium | low
    file: Mapped[str] = mapped_column(String(1000))
    line_start: Mapped[int] = mapped_column(Integer)
    line_end: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300))
    evidence: Mapped[str] = mapped_column(Text)
    explanation: Mapped[str] = mapped_column(Text)
    suggested_fix: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)
    needs_human_review: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|confirmed|rejected|fixed
    created_at: Mapped[datetime] = mapped_column(default=_now)


class AnalysisRun(Base):
    """One coordinator + specialists pass over a repo (Week 3)."""

    __tablename__ = "analysis_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|running|done|failed
    model: Mapped[str] = mapped_column(String(100))
    error: Mapped[str | None] = mapped_column(Text)
    plan: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # role -> {files, reason}
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)


class Fix(Base):
    """A proposed patch for one finding (Week 6). A PR is only opened on explicit request."""

    __tablename__ = "fixes"
    id: Mapped[int] = mapped_column(primary_key=True)
    finding_id: Mapped[int] = mapped_column(ForeignKey("findings.id", ondelete="CASCADE"), index=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|running|ready|failed|pr_opened
    summary: Mapped[str] = mapped_column(Text, default="")
    commit_message: Mapped[str] = mapped_column(String(200), default="")
    diff: Mapped[str] = mapped_column(Text, default="")
    files_changed: Mapped[int] = mapped_column(Integer, default=0)
    tests_passed: Mapped[bool | None] = mapped_column(default=None)
    test_output: Mapped[str] = mapped_column(Text, default="")
    pr_url: Mapped[str | None] = mapped_column(String(500))
    error: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=_now)
