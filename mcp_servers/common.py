"""Shared plumbing for the MCP servers: DB sessions and event publishing.

The servers read the same database as the API (DATABASE_URL). Writes are recorded as events like
everything else; with REDIS_URL set they also reach open browsers live, otherwise on reconnect.
"""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from backend.config import get_settings
from backend.db.session import make_engine, make_session_factory
from backend.ingest.runner import Publish


def default_sessions() -> sessionmaker[Session]:
    return make_session_factory(make_engine(get_settings().database_url))


def default_publisher() -> Publish | None:
    url = get_settings().redis_url
    if not url:
        return None
    from backend.events.relay import redis_publisher

    return redis_publisher(url)
