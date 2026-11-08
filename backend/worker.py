"""arq worker (Week 4). Run with REDIS_URL set:

    .venv/Scripts/python -m arq backend.worker.WorkerSettings

Each job runs the same function the in-process mode runs, in a thread, publishing events to Redis
for the API to relay. Events are stored first, so nothing is lost if Redis drops a message.
"""

from __future__ import annotations

import asyncio
from typing import Any

from backend.agents.runner import execute_analysis
from backend.config import get_settings
from backend.db.session import make_engine, make_session_factory
from backend.events.relay import redis_publisher
from backend.fixer.runner import execute_coverage, execute_fix, execute_open_pr
from backend.ingest.runner import execute_ingest


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    if not settings.redis_url:
        raise RuntimeError("REDIS_URL must be set for the worker.")
    ctx["settings"] = settings
    ctx["sessions"] = make_session_factory(make_engine(settings.database_url))
    ctx["publish"] = redis_publisher(settings.redis_url)


async def ingest(ctx: dict[str, Any], repo_id: int) -> None:
    await asyncio.to_thread(execute_ingest, repo_id, ctx["settings"], ctx["sessions"], ctx["publish"])


async def analyze(ctx: dict[str, Any], run_id: int) -> None:
    await asyncio.to_thread(execute_analysis, run_id, ctx["settings"], ctx["sessions"], ctx["publish"])


async def fix(ctx: dict[str, Any], fix_id: int) -> None:
    await asyncio.to_thread(execute_fix, fix_id, ctx["settings"], ctx["sessions"], ctx["publish"])


async def open_pr(ctx: dict[str, Any], fix_id: int) -> None:
    await asyncio.to_thread(execute_open_pr, fix_id, ctx["settings"], ctx["sessions"], ctx["publish"])


async def coverage(ctx: dict[str, Any], repo_id: int) -> None:
    await asyncio.to_thread(execute_coverage, repo_id, ctx["settings"], ctx["sessions"])


def _redis_settings():
    from arq.connections import RedisSettings

    url = get_settings().redis_url or "redis://localhost:6379"
    return RedisSettings.from_dsn(url)


class WorkerSettings:
    functions = [ingest, analyze, fix, open_pr, coverage]
    on_startup = startup
    max_jobs = 4
    job_timeout = 3600  # agent runs and sandboxed test suites can be slow
    redis_settings = _redis_settings()
