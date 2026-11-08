"""Run an ingest for a Repo row: record + publish every event, persist the result."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from backend.config import Settings
from backend.db.models import District, EventRow, File, ImportEdge, Repo, Road
from backend.events.schema import EventEnvelope, IngestCompleted, IngestFailed, Payload

from .pipeline import IngestResult, run_ingest

log = logging.getLogger(__name__)
Publish = Callable[[EventEnvelope], None]


class RunRecorder:
    """Assigns sequence numbers, stores each event, then publishes it.

    Store-then-publish means a late WebSocket subscriber can replay from the database
    and then switch to live events without a gap (it de-duplicates by seq).
    """

    # One lock per repo, shared by every recorder in this process: an analysis and a fix on the
    # same repo write to one event stream, and seq must stay gap-free and in publish order.
    _locks: dict[int, threading.Lock] = {}
    _locks_guard = threading.Lock()

    def __init__(self, repo_id: int, sessions: sessionmaker[Session], publish: Publish | None):
        self.repo_id = repo_id
        self.sessions = sessions
        self.publish = publish
        with RunRecorder._locks_guard:
            self._lock = RunRecorder._locks.setdefault(repo_id, threading.Lock())

    def __call__(self, payload: Payload) -> EventEnvelope:
        with self._lock:
            # Re-read the last seq each time: other recorders (or worker processes) may have written.
            # Across processes the unique (repo_id, seq) constraint decides; the loser retries.
            for attempt in range(5):
                with self.sessions() as s:
                    last = s.scalar(
                        select(EventRow.seq)
                        .where(EventRow.repo_id == self.repo_id)
                        .order_by(EventRow.seq.desc())
                        .limit(1)
                    )
                    env = EventEnvelope.wrap(self.repo_id, (last or 0) + 1, payload)
                    s.add(EventRow(repo_id=env.repo_id, seq=env.seq, type=env.type, data=env.data, ts=env.ts))
                    try:
                        s.commit()
                        break
                    except IntegrityError:
                        s.rollback()
                        if attempt == 4:
                            raise
            if self.publish:
                self.publish(env)
            return env


def _set_status(sessions: sessionmaker[Session], repo_id: int, **fields) -> None:
    with sessions() as s:
        repo = s.get(Repo, repo_id)
        for k, v in fields.items():
            setattr(repo, k, v)
        s.commit()


def persist_result(s: Session, repo_id: int, r: IngestResult) -> None:
    for model in (File, District, Road, ImportEdge):
        s.execute(delete(model).where(model.repo_id == repo_id))
    s.add_all(District(repo_id=repo_id, **d.model_dump(exclude={"folders"})) for d in r.districts)
    s.add_all(File(repo_id=repo_id, **b.model_dump()) for b in r.buildings)
    s.add_all(Road(repo_id=repo_id, src=x.src, dst=x.dst, imports=x.imports) for x in r.roads)
    s.add_all(ImportEdge(repo_id=repo_id, src_path=a, dst_path=b) for a, b in r.edges)
    repo = s.get(Repo, repo_id)
    repo.name = r.name
    repo.commit_sha = r.commit_sha
    repo.file_count = len(r.buildings)
    repo.status = "ready"
    repo.error = None
    repo.stats = {
        "duration_s": r.duration_s,
        "skipped": r.skipped,
        "truncated": r.truncated,
        "edges": len(r.edges),
        "top_external_imports": r.external_imports.most_common(20),
    }
    s.commit()


def execute_ingest(
    repo_id: int,
    settings: Settings,
    sessions: sessionmaker[Session],
    publish: Publish | None = None,
    *,
    allow_local: bool | None = None,
    raise_errors: bool = False,
) -> IngestResult | None:
    with sessions() as s:
        source = s.get(Repo, repo_id).source
    recorder = RunRecorder(repo_id, sessions, publish)
    _set_status(sessions, repo_id, status="running")
    try:
        result = run_ingest(source, recorder, settings, allow_local=allow_local)
        with sessions() as s:
            persist_result(s, repo_id, result)
        recorder(
            IngestCompleted(
                commit_sha=result.commit_sha,
                files=len(result.buildings),
                districts=len(result.districts),
                roads=len(result.roads),
                edges=len(result.edges),
                duration_s=result.duration_s,
                skipped=result.skipped,
                truncated=result.truncated,
            )
        )
        return result
    except Exception as exc:  # contain the failure: the run is marked failed, the server lives on
        log.exception("ingest %s failed", repo_id)
        message = str(exc)[:1000] or type(exc).__name__
        _set_status(sessions, repo_id, status="failed", error=message)
        recorder(IngestFailed(error=message))
        if raise_errors:
            raise
        return None
