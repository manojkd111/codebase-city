"""FastAPI app: repo commands in, typed city/agent events out over WebSockets.

Run:  uvicorn backend.api.main:app --reload
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select

from backend.agents.runner import ACTIVE, expire_stale_runs
from backend.config import Settings, get_settings
from backend.db.models import AnalysisRun, District, EventRow, File, Finding, Fix, ImportEdge, Repo, Road
from backend.db.session import make_engine, make_session_factory
from backend.events.bus import EventBus
from backend.events.schema import AnalysisFailed, EventEnvelope, FindingUpdated
from backend.ingest.runner import RunRecorder
from backend.ingest.source import parse_github_url
from backend.jobs import InProcessJobs, Jobs, QueuedJobs
from backend.views import file_dict, finding_dict, fix_dict, repo_dict, run_dict


class CreateRepo(BaseModel):
    url: str


class UpdateFinding(BaseModel):
    status: Literal["open", "confirmed", "rejected"]


def create_app(settings: Settings | None = None, *, llm_client_factory: Callable[[], Any] | None = None) -> FastAPI:
    """`llm_client_factory` lets tests inject a fake Claude client; production builds a real one per run."""
    settings = settings or get_settings()
    sessions = make_session_factory(make_engine(settings.database_url))
    bus = EventBus()
    state: dict[str, Any] = {}

    def publisher():
        loop = asyncio.get_running_loop()
        return lambda env: loop.call_soon_threadsafe(bus.publish, env)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        relay_task = None
        if settings.redis_url:
            # Week 4: jobs run in arq workers; their events arrive over Redis and fan out to WebSockets.
            from arq import create_pool
            from arq.connections import RedisSettings
            from redis.asyncio import Redis

            from backend.events.relay import relay_to_bus

            state["jobs"] = QueuedJobs(await create_pool(RedisSettings.from_dsn(settings.redis_url)))
            relay_task = asyncio.create_task(relay_to_bus(Redis.from_url(settings.redis_url), bus))
        else:
            state["jobs"] = InProcessJobs(settings, sessions, publisher, llm_client_factory)
        expire_stale()  # runs a previous server left behind when it stopped mid-run
        yield
        state["jobs"].cancel_all()
        if relay_task:
            relay_task.cancel()

    app = FastAPI(title="Codebase City", version="0.2.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"], allow_headers=["*"]
    )
    app.state.sessions = sessions
    app.state.bus = bus

    def jobs() -> Jobs:
        return state["jobs"]

    def record(repo_id: int, payload) -> None:
        RunRecorder(repo_id, sessions, publisher())(payload)

    def expire_stale(repo_id: int | None = None) -> None:
        """Fail runs whose process died, so they stop blocking new runs (and the UI recalls their drones)."""
        with sessions() as s:
            expired = expire_stale_runs(s, repo_id)
            s.commit()
        for rid, run_id, error in expired:
            record(rid, AnalysisFailed(run_id=run_id, error=error))

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True}

    @app.post("/api/repos", status_code=202)
    async def create_repo(body: CreateRepo) -> dict:
        url = body.url.strip()
        if not parse_github_url(url) and not settings.allow_local_sources:
            raise HTTPException(422, "Expected a public https://github.com/<owner>/<repo> URL.")
        with sessions() as s:
            repo = Repo(source=url, status="queued")
            s.add(repo)
            s.commit()
            repo_id = repo.id
        await jobs().ingest(repo_id)
        return {"id": repo_id, "status": "queued", "events": f"/ws/repos/{repo_id}"}

    @app.get("/api/repos")
    def list_repos() -> list[dict]:
        with sessions() as s:
            return [repo_dict(r) for r in s.scalars(select(Repo).order_by(Repo.id.desc()).limit(100))]

    def _get_repo(s, repo_id: int) -> Repo:
        repo = s.get(Repo, repo_id)
        if repo is None:
            raise HTTPException(404, "repo not found")
        return repo

    @app.get("/api/repos/{repo_id}")
    def get_repo(repo_id: int) -> dict:
        with sessions() as s:
            return repo_dict(_get_repo(s, repo_id))

    @app.get("/api/repos/{repo_id}/city")
    def get_city(repo_id: int) -> dict:
        """Snapshot of the finished city (use the WebSocket to watch it being built)."""
        with sessions() as s:
            repo = _get_repo(s, repo_id)
            return {
                "repo": repo_dict(repo),
                "districts": [
                    {"name": d.name, "file_count": d.file_count, "total_bytes": d.total_bytes}
                    for d in s.scalars(select(District).where(District.repo_id == repo_id))
                ],
                "buildings": [file_dict(f) for f in s.scalars(select(File).where(File.repo_id == repo_id))],
                "roads": [
                    {"src": r.src, "dst": r.dst, "imports": r.imports}
                    for r in s.scalars(select(Road).where(Road.repo_id == repo_id))
                ],
            }

    @app.get("/api/repos/{repo_id}/hotspots")
    def hotspots(
        repo_id: int,
        limit: int = Query(20, ge=1, le=200),
        district: str | None = None,
        include_tests: bool = False,
    ) -> list[dict]:
        """Riskiest files first. Test files are excluded by default: their many small
        functions inflate summed complexity without being where bugs live."""
        with sessions() as s:
            _get_repo(s, repo_id)
            q = select(File).where(File.repo_id == repo_id)
            if not include_tests:
                q = q.where(File.is_test.is_(False))
            if district:
                q = q.where(File.district == district)
            return [file_dict(f) for f in s.scalars(q.order_by(File.risk.desc()).limit(limit))]

    @app.get("/api/repos/{repo_id}/imports")
    def file_imports(repo_id: int, path: str) -> dict:
        """Import neighbours of one file inside the repo: powers "Trace dependencies" in the city."""
        with sessions() as s:
            _get_repo(s, repo_id)
            out = s.scalars(
                select(ImportEdge.dst_path).where(ImportEdge.repo_id == repo_id, ImportEdge.src_path == path)
            ).all()
            inc = s.scalars(
                select(ImportEdge.src_path).where(ImportEdge.repo_id == repo_id, ImportEdge.dst_path == path)
            ).all()
            return {"path": path, "imports": sorted(set(out)), "imported_by": sorted(set(inc))}

    # ---- agents (Week 3) ----

    @app.post("/api/repos/{repo_id}/analyze", status_code=202)
    async def analyze(repo_id: int) -> dict:
        """Start a coordinator + specialists pass. One active run per repo."""
        expire_stale(repo_id)  # a run whose process died must not block this one
        with sessions() as s:
            repo = _get_repo(s, repo_id)
            if repo.status != "ready":
                raise HTTPException(409, "The city must finish building before agents can inspect it.")
            active = s.scalar(select(AnalysisRun).where(AnalysisRun.repo_id == repo_id, AnalysisRun.status.in_(ACTIVE)))
            if active:
                raise HTTPException(409, f"Analysis run {active.id} is already in progress.")
            run = AnalysisRun(repo_id=repo_id, model=settings.model, status="queued")
            s.add(run)
            s.commit()
            run_id = run.id
        await jobs().analyze(run_id)
        return {"run_id": run_id, "status": "queued", "events": f"/ws/repos/{repo_id}"}

    @app.post("/api/analyses/{run_id}/stop", status_code=202)
    async def stop_analysis(run_id: int) -> dict:  # async: publishing needs the event loop
        """Ask a run to stop. Each agent finishes the turn it's on (seconds) and stops; findings so far
        are kept. The run reports `analysis.cancelled` on the event stream once it has stopped."""
        with sessions() as s:
            run = s.get(AnalysisRun, run_id)
            if run is None:
                raise HTTPException(404, "Analysis run not found.")
            repo_id = run.repo_id
        # Stopping a run whose process already died would wait forever; end it now instead.
        expire_stale(repo_id)
        with sessions() as s:
            run = s.get(AnalysisRun, run_id)
            if run.status not in ACTIVE:
                raise HTTPException(409, f"Analysis run {run_id} has already ended ({run.status}).")
            run.status = "stopping"
            s.commit()
        return {"run_id": run_id, "status": "stopping"}

    @app.get("/api/repos/{repo_id}/analysis")
    def latest_analysis(repo_id: int) -> dict | None:
        with sessions() as s:
            _get_repo(s, repo_id)
            run = s.scalar(
                select(AnalysisRun).where(AnalysisRun.repo_id == repo_id).order_by(AnalysisRun.id.desc()).limit(1)
            )
            return run_dict(run) if run else None

    @app.get("/api/repos/{repo_id}/findings")
    def list_findings(
        repo_id: int,
        status: str | None = None,
        category: str | None = None,
        file: str | None = None,
    ) -> list[dict]:
        with sessions() as s:
            _get_repo(s, repo_id)
            q = select(Finding).where(Finding.repo_id == repo_id)
            if status:
                q = q.where(Finding.status == status)
            if category:
                q = q.where(Finding.category == category)
            if file:
                q = q.where(Finding.file == file)
            return [finding_dict(f) for f in s.scalars(q.order_by(Finding.id))]

    @app.get("/api/findings/{finding_id}")
    def get_finding(finding_id: int) -> dict:
        with sessions() as s:
            f = s.get(Finding, finding_id)
            if f is None:
                raise HTTPException(404, "finding not found")
            return finding_dict(f)

    @app.patch("/api/findings/{finding_id}")
    async def update_finding(finding_id: int, body: UpdateFinding) -> dict:
        """Human triage. `fixed` is only set by the fixer, never by hand."""
        with sessions() as s:
            f = s.get(Finding, finding_id)
            if f is None:
                raise HTTPException(404, "finding not found")
            if f.status == "fixed":
                raise HTTPException(409, "This finding already has a merged fix.")
            f.status = body.status
            s.commit()
            out = finding_dict(f)
        record(out["repo_id"], FindingUpdated(finding_id=finding_id, status=body.status))
        return out

    # ---- fixes (Week 6) ----

    @app.post("/api/findings/{finding_id}/fix", status_code=202)
    async def start_fix(finding_id: int) -> dict:
        """Generate a patch for a finding a human has confirmed. Nothing leaves the server yet."""
        with sessions() as s:
            f = s.get(Finding, finding_id)
            if f is None:
                raise HTTPException(404, "finding not found")
            if f.status != "confirmed":
                raise HTTPException(409, "Confirm the finding before asking for a fix.")
            busy = s.scalar(select(Fix).where(Fix.finding_id == finding_id, Fix.status.in_(("queued", "running"))))
            if busy:
                raise HTTPException(409, f"Fix {busy.id} is already being generated.")
            fix = Fix(finding_id=finding_id, repo_id=f.repo_id, status="queued")
            s.add(fix)
            s.commit()
            out = fix_dict(fix)
        await jobs().fix(out["id"])
        return out

    @app.get("/api/findings/{finding_id}/fixes")
    def list_fixes(finding_id: int) -> list[dict]:
        with sessions() as s:
            return [
                fix_dict(x) for x in s.scalars(select(Fix).where(Fix.finding_id == finding_id).order_by(Fix.id.desc()))
            ]

    @app.get("/api/fixes/{fix_id}")
    def get_fix(fix_id: int) -> dict:
        with sessions() as s:
            fix = s.get(Fix, fix_id)
            if fix is None:
                raise HTTPException(404, "fix not found")
            return fix_dict(fix)

    @app.post("/api/fixes/{fix_id}/pr", status_code=202)
    async def open_pr(fix_id: int) -> dict:
        """Outward-facing: pushes a branch and opens a PR on GitHub. Only on this explicit request."""
        if not settings.github_token:
            raise HTTPException(409, "Set GITHUB_TOKEN on the server to open pull requests.")
        with sessions() as s:
            fix = s.get(Fix, fix_id)
            if fix is None:
                raise HTTPException(404, "fix not found")
            if fix.status == "pr_opened":
                raise HTTPException(409, f"A pull request is already open: {fix.pr_url}")
            if fix.status != "ready":
                raise HTTPException(409, "The fix isn't ready.")
            if s.get(Finding, fix.finding_id).status != "confirmed":
                raise HTTPException(409, "The finding is no longer confirmed.")
        await jobs().open_pr(fix_id)
        return {"fix_id": fix_id, "status": "opening"}

    @app.post("/api/repos/{repo_id}/coverage", status_code=202)
    async def collect_coverage(repo_id: int) -> dict:
        """Run the repo's tests with coverage in the Docker sandbox (ADR 0003); updates risk."""
        with sessions() as s:
            if _get_repo(s, repo_id).status != "ready":
                raise HTTPException(409, "The city must finish building first.")
        await jobs().coverage(repo_id)
        return {"repo_id": repo_id, "status": "queued"}

    @app.get("/api/config")
    def config() -> dict:
        """What this server can do, so the UI can explain disabled buttons instead of failing."""
        import os

        return {
            "model": settings.model,
            "llm_credentials": bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))
            or llm_client_factory is not None,
            "pull_requests": bool(settings.github_token),
            "workers": bool(settings.redis_url),
        }

    def _load_events(repo_id: int, after_seq: int) -> list[EventEnvelope]:
        with sessions() as s:
            rows = s.scalars(
                select(EventRow).where(EventRow.repo_id == repo_id, EventRow.seq > after_seq).order_by(EventRow.seq)
            )
            return [EventEnvelope(repo_id=r.repo_id, seq=r.seq, type=r.type, ts=r.ts, data=r.data) for r in rows]

    @app.websocket("/ws/repos/{repo_id}")
    async def events_ws(ws: WebSocket, repo_id: int, after_seq: int = 0) -> None:
        """Replays stored events after `after_seq`, then streams live ones."""
        await ws.accept()
        q = bus.subscribe(repo_id)  # subscribe BEFORE replay so nothing falls in the gap
        try:
            last = after_seq
            for env in await asyncio.to_thread(_load_events, repo_id, after_seq):
                await ws.send_json(env.model_dump(mode="json"))
                last = env.seq
            while True:
                env = await q.get()
                if env.seq <= last:
                    continue
                await ws.send_json(env.model_dump(mode="json"))
                last = env.seq
        except WebSocketDisconnect:
            pass
        finally:
            bus.unsubscribe(repo_id, q)

    return app


app = create_app()
