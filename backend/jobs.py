"""Where long-running work runs: threads in the API process, or arq workers via Redis (Week 4).

The API only calls these four methods, so switching is a config change (REDIS_URL), not a code change.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Protocol

from sqlalchemy.orm import Session, sessionmaker

from backend.agents.runner import execute_analysis
from backend.config import Settings
from backend.fixer.runner import execute_coverage, execute_fix, execute_open_pr
from backend.ingest.runner import Publish, execute_ingest


class Jobs(Protocol):
    async def ingest(self, repo_id: int) -> None: ...
    async def analyze(self, run_id: int) -> None: ...
    async def fix(self, fix_id: int) -> None: ...
    async def open_pr(self, fix_id: int) -> None: ...
    async def coverage(self, repo_id: int) -> None: ...


class InProcessJobs:
    """Default for local dev: each job runs in a thread; events go straight to the in-process bus."""

    def __init__(
        self,
        settings: Settings,
        sessions: sessionmaker[Session],
        publish: Callable[[], Publish],
        llm_client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.settings, self.sessions, self.publish = settings, sessions, publish
        self.llm_client_factory = llm_client_factory
        self.ingest_slots = asyncio.Semaphore(settings.max_concurrent_ingests)
        self.tasks: set[asyncio.Task] = set()  # hold references so running tasks aren't GC'd

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def _client(self) -> Any:
        return self.llm_client_factory() if self.llm_client_factory else None

    async def ingest(self, repo_id: int) -> None:
        publish = self.publish()

        async def run() -> None:
            async with self.ingest_slots:
                await asyncio.to_thread(execute_ingest, repo_id, self.settings, self.sessions, publish)

        self._spawn(run())

    async def analyze(self, run_id: int) -> None:
        self._spawn(
            asyncio.to_thread(
                execute_analysis, run_id, self.settings, self.sessions, self.publish(), client=self._client()
            )
        )

    async def fix(self, fix_id: int) -> None:
        self._spawn(
            asyncio.to_thread(execute_fix, fix_id, self.settings, self.sessions, self.publish(), client=self._client())
        )

    async def open_pr(self, fix_id: int) -> None:
        self._spawn(asyncio.to_thread(execute_open_pr, fix_id, self.settings, self.sessions, self.publish()))

    async def coverage(self, repo_id: int) -> None:
        self._spawn(asyncio.to_thread(execute_coverage, repo_id, self.settings, self.sessions))

    def cancel_all(self) -> None:
        for t in self.tasks:
            t.cancel()


class QueuedJobs:
    """REDIS_URL set: enqueue to arq; `python -m arq backend.worker.WorkerSettings` runs them."""

    def __init__(self, pool) -> None:
        self.pool = pool

    async def ingest(self, repo_id: int) -> None:
        await self.pool.enqueue_job("ingest", repo_id)

    async def analyze(self, run_id: int) -> None:
        await self.pool.enqueue_job("analyze", run_id)

    async def fix(self, fix_id: int) -> None:
        await self.pool.enqueue_job("fix", fix_id)

    async def open_pr(self, fix_id: int) -> None:
        await self.pool.enqueue_job("open_pr", fix_id)

    async def coverage(self, repo_id: int) -> None:
        await self.pool.enqueue_job("coverage", repo_id)

    def cancel_all(self) -> None:
        pass
