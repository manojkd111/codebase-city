"""Run one analysis: coordinator plan, then specialists in parallel, every step an event."""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.config import Settings
from backend.db.models import AnalysisRun, File, Finding, ImportEdge, Repo
from backend.events.schema import (
    AgentDispatched,
    AgentFinished,
    AnalysisCancelled,
    AnalysisCompleted,
    AnalysisFailed,
    AnalysisStarted,
    FindingSubmitted,
)
from backend.ingest.runner import Publish, RunRecorder
from backend.ingest.source import checkout, resolve_source

from . import llm
from .coordinator import make_plan
from .specialist import SpecialistResult, run_specialist
from .tools import ROLES, AcceptedFinding, FindingRegistry, ToolBox
from .workspace import Workspace

log = logging.getLogger(__name__)

METRIC_FIELDS = (
    "loc",
    "functions",
    "complexity_total",
    "complexity_max",
    "churn_30",
    "churn_90",
    "coverage",
    "risk",
    "is_test",
    "language",
    "district",
)


Graph = dict[str, list[str]]


def load_repo_facts(s: Session, repo_id: int) -> tuple[list[dict[str, Any]], Graph, Graph]:
    files = [
        {"path": f.path, **{k: getattr(f, k) for k in METRIC_FIELDS}}
        for f in s.scalars(select(File).where(File.repo_id == repo_id).order_by(File.path))
    ]
    imports: dict[str, list[str]] = defaultdict(list)
    imported_by: dict[str, list[str]] = defaultdict(list)
    for e in s.scalars(select(ImportEdge).where(ImportEdge.repo_id == repo_id).order_by(ImportEdge.id)):
        imports[e.src_path].append(e.dst_path)
        imported_by[e.dst_path].append(e.src_path)
    return files, dict(imports), dict(imported_by)


@contextmanager
def repo_checkout(source: str, commit_sha: str | None, *, allow_local: bool, workdir: str | None):
    """The repo at the ingested commit, on local disk, deleted afterwards if it was cloned."""
    with resolve_source(source, allow_local=allow_local, workdir=workdir) as src:
        if commit_sha and src.is_git and not checkout(src.path, commit_sha):
            log.warning("could not check out %s; using HEAD", commit_sha)
        yield src.path


def execute_analysis(
    run_id: int,
    settings: Settings,
    sessions: sessionmaker[Session],
    publish: Publish | None = None,
    *,
    client: Any = None,
    allow_local: bool | None = None,
    workspace_root: Path | None = None,
) -> list[SpecialistResult]:
    started = time.monotonic()
    with sessions() as s:
        run = s.get(AnalysisRun, run_id)
        repo = s.get(Repo, run.repo_id)
        repo_id, source, name, sha = repo.id, repo.source, repo.name or repo.source, repo.commit_sha or ""
        files, imports, imported_by = load_repo_facts(s, repo_id)
        stopped_while_queued = run.status == "stopping"
        if not stopped_while_queued:
            run.status = "running"
        s.commit()

    emit = RunRecorder(repo_id, sessions, publish)

    def stop_requested() -> bool:
        # Read from the database, not memory: with REDIS_URL the run is in another process (ADR 0007).
        with sessions() as s:
            return s.scalar(select(AnalysisRun.status).where(AnalysisRun.id == run_id)) == "stopping"

    def finish(results: list[SpecialistResult], usage: llm.Usage, *, cancelled: bool) -> list[SpecialistResult]:
        total = sum((r.usage for r in results), usage)
        with sessions() as s:
            run = s.get(AnalysisRun, run_id)
            run.status = "cancelled" if cancelled else "done"
            run.input_tokens, run.output_tokens = total.input_tokens, total.output_tokens
            run.finished_at = datetime.now(UTC)
            s.commit()
        event = AnalysisCancelled if cancelled else AnalysisCompleted
        emit(
            event(
                run_id=run_id,
                findings=sum(len(r.finding_ids) for r in results),
                duration_s=round(time.monotonic() - started, 2),
                input_tokens=total.input_tokens,
                output_tokens=total.output_tokens,
                cache_read_tokens=total.cache_read,
                cost_usd=total.cost_usd(settings.model),
            )
        )
        return results

    if stopped_while_queued:
        return finish([], llm.Usage(), cancelled=True)

    emit(AnalysisStarted(run_id=run_id, model=settings.model))
    results: list[SpecialistResult] = []
    try:
        client = client or llm.make_client()
        plan = make_plan(
            client,
            model=settings.model,
            effort=settings.coordinator_effort,
            repo_name=name,
            files=files,
            imported_by=imported_by,
            per_role=settings.agent_files,
        )
        with sessions() as s:
            s.get(AnalysisRun, run_id).plan = plan.to_json()
            s.commit()
        if stop_requested():  # stopped while the coordinator was planning: don't clone or dispatch
            return finish([], plan.usage, cancelled=True)

        metrics = {f["path"]: {k: v for k, v in f.items() if k != "path"} for f in files}
        allow = settings.allow_local_sources if allow_local is None else allow_local
        checkout_ctx = (
            nullcontext(workspace_root)
            if workspace_root
            else repo_checkout(source, sha, allow_local=allow, workdir=settings.workdir)
        )
        with checkout_ctx as root:
            workspace = Workspace(Path(root), [f["path"] for f in files])

            def on_finding(agent_id: str):
                def store(f: AcceptedFinding) -> int:
                    with sessions() as s:
                        row = Finding(repo_id=repo_id, run_id=run_id, agent_id=agent_id, **f.__dict__)
                        s.add(row)
                        s.commit()
                        fid = row.id
                    emit(
                        FindingSubmitted(
                            run_id=run_id,
                            agent_id=agent_id,
                            finding_id=fid,
                            category=f.category,
                            severity=f.severity,
                            file=f.file,
                            line_start=f.line_start,
                            line_end=f.line_end,
                            title=f.title,
                            confidence=f.confidence,
                        )
                    )
                    return fid

                return store

            registry = FindingRegistry()  # one per run: agents don't store the same problem twice
            jobs = []
            for role in ROLES:
                assigned = plan.assignments.get(role, [])
                if not assigned:
                    continue
                agent_id = f"{role}-{run_id}"
                emit(
                    AgentDispatched(
                        run_id=run_id, agent_id=agent_id, role=role, files=assigned, reason=plan.reasons.get(role, "")
                    )
                )
                toolbox = ToolBox(
                    role, workspace, metrics, imports, imported_by, on_finding(agent_id), registry=registry
                )
                jobs.append((agent_id, role, assigned, toolbox))

            def work(job):
                agent_id, role, assigned, toolbox = job
                r = run_specialist(
                    client,
                    model=settings.model,
                    effort=settings.effort,
                    max_turns=settings.agent_max_turns,
                    run_id=run_id,
                    agent_id=agent_id,
                    role=role,
                    files=assigned,
                    repo_name=name,
                    commit=sha,
                    toolbox=toolbox,
                    emit=emit,
                    should_stop=stop_requested,
                )
                emit(
                    AgentFinished(
                        run_id=run_id,
                        agent_id=agent_id,
                        findings=len(r.finding_ids),
                        rejected=r.rejected,
                        turns=r.turns,
                        input_tokens=r.usage.input_tokens,
                        output_tokens=r.usage.output_tokens,
                        error=r.error,
                        cache_read_tokens=r.usage.cache_read,
                        cost_usd=r.usage.cost_usd(settings.model),
                        stopped=r.stopped,
                    )
                )
                return r

            with ThreadPoolExecutor(max_workers=max(1, settings.max_concurrent_agents)) as pool:
                results = list(pool.map(work, jobs))

        # A stop that arrived after every agent had finished on its own changed nothing: that run is done.
        return finish(results, plan.usage, cancelled=any(r.stopped for r in results))
    except Exception as exc:
        log.exception("analysis %s failed", run_id)
        message = str(exc)[:1000] or type(exc).__name__
        with sessions() as s:
            run = s.get(AnalysisRun, run_id)
            run.status, run.error, run.finished_at = "failed", message, datetime.now(UTC)
            s.commit()
        emit(AnalysisFailed(run_id=run_id, error=message))
        return results
