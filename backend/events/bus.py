"""In-process pub/sub for live events.

Interface kept tiny (subscribe / unsubscribe / publish) so it can be swapped for
Redis pub/sub once ingestion moves to separate worker processes (Week 4).
"""

from __future__ import annotations

import asyncio
from collections import defaultdict

from .schema import EventEnvelope


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[int, set[asyncio.Queue[EventEnvelope]]] = defaultdict(set)

    def subscribe(self, repo_id: int) -> asyncio.Queue[EventEnvelope]:
        q: asyncio.Queue[EventEnvelope] = asyncio.Queue()
        self._subs[repo_id].add(q)
        return q

    def unsubscribe(self, repo_id: int, q: asyncio.Queue[EventEnvelope]) -> None:
        subs = self._subs.get(repo_id)
        if subs:
            subs.discard(q)
            if not subs:
                del self._subs[repo_id]

    def publish(self, env: EventEnvelope) -> None:
        """Must be called on the event loop thread (use loop.call_soon_threadsafe)."""
        for q in list(self._subs.get(env.repo_id, ())):
            q.put_nowait(env)
