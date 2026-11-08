"""Live events across processes (Week 4): workers publish to Redis, the API relays to its WebSockets.

Events are still stored before they are published (ADR 0002), so Redis only carries the live
tail; a subscriber that misses a message replays it from the database by seq.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from .bus import EventBus
from .schema import EventEnvelope

CHANNEL = "cbc:events"
log = logging.getLogger(__name__)


def redis_publisher(url: str) -> Callable[[EventEnvelope], None]:
    import redis

    client = redis.Redis.from_url(url)

    def publish(env: EventEnvelope) -> None:
        try:
            client.publish(CHANNEL, env.model_dump_json())
        except redis.RedisError:  # the DB row exists; clients will catch up on reconnect
            log.warning("could not publish event %s/%s to redis", env.repo_id, env.seq)

    return publish


async def relay_to_bus(redis_client, bus: EventBus, stop: asyncio.Event | None = None) -> None:
    """Forward every event on CHANNEL into the in-process bus. Runs on the API's event loop."""
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(CHANNEL)
    try:
        while stop is None or not stop.is_set():
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if msg is None:
                continue
            try:
                bus.publish(EventEnvelope.model_validate_json(msg["data"]))
            except ValueError:
                log.warning("dropping malformed event on %s", CHANNEL)
    finally:
        await pubsub.unsubscribe(CHANNEL)
        await pubsub.aclose()
