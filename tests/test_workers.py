from __future__ import annotations

import asyncio

import fakeredis
import fakeredis.aioredis

from backend.events.bus import EventBus
from backend.events.relay import CHANNEL, relay_to_bus
from backend.events.schema import EventEnvelope, IngestStarted
from backend.jobs import QueuedJobs


def test_worker_events_reach_api_websocket_subscribers_via_redis():
    async def scenario():
        server = fakeredis.FakeServer()
        api_side = fakeredis.aioredis.FakeRedis(server=server)
        worker_side = fakeredis.FakeRedis(server=server)  # workers publish synchronously from threads
        bus = EventBus()
        q = bus.subscribe(7)
        stop = asyncio.Event()
        task = asyncio.create_task(relay_to_bus(api_side, bus, stop))
        await asyncio.sleep(0.05)  # let the relay subscribe
        env = EventEnvelope.wrap(7, 1, IngestStarted(source="https://github.com/a/b"))
        worker_side.publish(CHANNEL, env.model_dump_json())
        worker_side.publish(CHANNEL, b"not json")  # dropped, relay keeps going
        worker_side.publish(CHANNEL, EventEnvelope.wrap(7, 2, IngestStarted(source="x")).model_dump_json())
        got = [await asyncio.wait_for(q.get(), 2), await asyncio.wait_for(q.get(), 2)]
        stop.set()
        await asyncio.wait_for(task, 3)
        return got

    got = asyncio.run(scenario())
    assert [e.seq for e in got] == [1, 2] and got[0].type == "ingest.started"


def test_queued_jobs_enqueue_by_name():
    class Pool:
        def __init__(self):
            self.jobs = []

        async def enqueue_job(self, name, *args):
            self.jobs.append((name, *args))

    pool = Pool()
    jobs = QueuedJobs(pool)

    async def go():
        await jobs.ingest(1)
        await jobs.analyze(2)
        await jobs.fix(3)
        await jobs.open_pr(3)
        await jobs.coverage(1)

    asyncio.run(go())
    assert pool.jobs == [("ingest", 1), ("analyze", 2), ("fix", 3), ("open_pr", 3), ("coverage", 1)]


def test_worker_module_exposes_every_job():
    from backend.worker import WorkerSettings

    assert {f.__name__ for f in WorkerSettings.functions} == {"ingest", "analyze", "fix", "open_pr", "coverage"}
