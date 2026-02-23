"""
Tests for web UI stability:
- EventBus thread-safety and delivery
- Terminal SSE events (done/error)
- Concurrent job submit
- Database write safety
"""
import asyncio
import json
import threading
import time
import unittest
from unittest.mock import patch, MagicMock

from app.config import Config
from app.db.database import Database
from app.utils.url_parser import TelegramLink
from app.web.services.event_bus import EventBus


class TestEventBusThreadSafety(unittest.TestCase):
    """EventBus must handle concurrent subscribe/unsubscribe/publish safely."""

    def test_subscribe_unsubscribe(self):
        bus = EventBus()
        q = bus.subscribe()
        self.assertEqual(bus.subscriber_count, 1)
        bus.unsubscribe(q)
        self.assertEqual(bus.subscriber_count, 0)

    def test_publish_delivers_to_subscribers(self):
        bus = EventBus()
        q = bus.subscribe()
        bus.publish({"type": "test", "data": "hello"})
        self.assertFalse(q.empty())
        event = q.get_nowait()
        self.assertEqual(event["type"], "test")
        bus.unsubscribe(q)

    def test_publish_to_empty_bus(self):
        bus = EventBus()
        # Should not raise
        bus.publish({"type": "test"})

    def test_concurrent_subscribe_publish(self):
        """Simulate concurrent subscribe/publish from different threads."""
        bus = EventBus()
        errors = []
        received = []

        def subscriber():
            try:
                q = bus.subscribe()
                time.sleep(0.1)  # Wait for publisher
                while not q.empty():
                    received.append(q.get_nowait())
                bus.unsubscribe(q)
            except Exception as e:
                errors.append(e)

        def publisher():
            try:
                for i in range(50):
                    bus.publish({"type": "test", "i": i})
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        threads = []
        for _ in range(3):
            threads.append(threading.Thread(target=subscriber))
        threads.append(threading.Thread(target=publisher))

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        self.assertEqual(errors, [], f"Thread errors: {errors}")

    def test_multiple_subscribers_all_receive(self):
        bus = EventBus()
        queues = [bus.subscribe() for _ in range(5)]
        bus.publish({"type": "broadcast"})

        for q in queues:
            self.assertFalse(q.empty())
            event = q.get_nowait()
            self.assertEqual(event["type"], "broadcast")

        for q in queues:
            bus.unsubscribe(q)


class TestTerminalEvents(unittest.TestCase):
    """_run_pipeline must always emit a terminal event (done or error)."""

    def setUp(self):
        self.cfg = Config()
        self.db = Database(":memory:")
        self.db.connect()
        self.db.migrate()

    def tearDown(self):
        self.db.close()

    def _create_test_job(self, url="https://t.me/c/123456789/1"):
        link = TelegramLink(chat_id=123456789, msg_id=1, raw_url=url)
        return self.db.create_job(link), link

    def test_error_on_auth_failure(self):
        """When Telegram is not authorized, error event must be sent."""
        from app.web.services.job_service import JobService, _notify
        from app.web.services.event_bus import EventBus

        bus = EventBus()
        q = bus.subscribe()
        events = []

        job_id, link = self._create_test_job()
        svc = JobService(self.cfg, self.db)

        # Patch event_bus and make_client to simulate auth failure
        with patch('app.web.services.job_service.event_bus', bus), \
             patch('app.web.services.job_service.make_client') as mock_client:
            client = MagicMock()

            # Create a real async coroutine for connect
            async def fake_connect():
                pass
            async def fake_disconnect():
                pass
            async def fake_is_authorized():
                return False

            client.connect = fake_connect
            client.disconnect = fake_disconnect
            client.is_user_authorized = fake_is_authorized
            mock_client.return_value = client

            svc._run_pipeline(job_id, link, False)

        # Collect events
        while not q.empty():
            events.append(q.get_nowait())

        # Must have at least one terminal event
        terminal = [e for e in events if e.get("status") in ("done", "error")]
        self.assertTrue(len(terminal) >= 1, f"No terminal event in: {events}")
        self.assertEqual(terminal[-1]["status"], "error")

        bus.unsubscribe(q)

    def test_worker_returns_none_sends_error(self):
        """When worker.process returns None, error event must be sent."""
        from app.web.services.job_service import JobService
        from app.web.services.event_bus import EventBus

        bus = EventBus()
        q = bus.subscribe()

        job_id, link = self._create_test_job()
        self.db.update_job_status(job_id, "error", last_error="test error")

        svc = JobService(self.cfg, self.db)

        with patch('app.web.services.job_service.event_bus', bus), \
             patch('app.web.services.job_service.make_client') as mock_client, \
             patch('app.web.services.job_service.Worker') as MockWorker:

            client = MagicMock()
            async def fake_connect(): pass
            async def fake_disconnect(): pass
            async def fake_is_authorized(): return True
            client.connect = fake_connect
            client.disconnect = fake_disconnect
            client.is_user_authorized = fake_is_authorized
            mock_client.return_value = client

            worker_inst = MagicMock()
            worker_inst.process.return_value = None  # Simulates failure
            MockWorker.return_value = worker_inst

            svc._run_pipeline(job_id, link, False)

        events = []
        while not q.empty():
            events.append(q.get_nowait())

        terminal = [e for e in events if e.get("status") in ("done", "error")]
        self.assertTrue(len(terminal) >= 1, f"No terminal event in: {events}")
        self.assertEqual(terminal[-1]["status"], "error")

        bus.unsubscribe(q)


class TestDatabaseWriteSafety(unittest.TestCase):
    """Concurrent writes must not corrupt data."""

    def test_concurrent_job_creation(self):
        db = Database(":memory:")
        db.connect()
        db.migrate()

        errors = []
        created_ids = []
        lock = threading.Lock()

        def create_job(i):
            try:
                link = TelegramLink(chat_id=100+i, msg_id=i, raw_url=f"https://t.me/c/{100+i}/{i}")
                job_id = db.create_job(link)
                with lock:
                    created_ids.append(job_id)
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=create_job, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        self.assertEqual(errors, [], f"Thread errors: {errors}")
        self.assertEqual(len(created_ids), 10)

        # Verify all jobs are in DB
        jobs = db.list_jobs()
        self.assertEqual(len(jobs), 10)
        db.close()

    def test_concurrent_status_updates(self):
        db = Database(":memory:")
        db.connect()
        db.migrate()

        link = TelegramLink(chat_id=999, msg_id=1, raw_url="https://t.me/c/999/1")
        job_id = db.create_job(link)

        errors = []
        statuses = ["downloading", "transcribing", "summarizing", "exporting", "done"]

        def update_status(status):
            try:
                db.update_job_status(job_id, status)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=update_status, args=(s,)) for s in statuses]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        self.assertEqual(errors, [], f"Thread errors: {errors}")

        # Job should be in one of the valid statuses
        job = db.get_job_by_id(job_id)
        self.assertIn(job["status"], statuses)
        db.close()


class TestConcurrentSubmit(unittest.TestCase):
    """Concurrent JobService.submit() must not race or corrupt DB."""

    def setUp(self):
        self.cfg = Config()
        self.db = Database(":memory:")
        self.db.connect()
        self.db.migrate()

    def tearDown(self):
        self.db.close()

    def test_parallel_submits_no_race(self):
        """Submit 5 different URLs concurrently — all must create jobs without errors."""
        from app.web.services.job_service import JobService
        from app.web.services.event_bus import EventBus

        bus = EventBus()
        svc = JobService(self.cfg, self.db)

        urls = [f"https://t.me/c/{2000+i}/{i}" for i in range(1, 6)]

        # Patch _run_pipeline to be a fast no-op (we test submit coordination, not pipeline)
        # Patch event_bus so events go to our test bus
        with patch.object(svc, '_run_pipeline', side_effect=lambda *a: None), \
             patch('app.web.services.job_service.event_bus', bus):

            async def run_submits():
                tasks = [svc.submit(url) for url in urls]
                return await asyncio.gather(*tasks, return_exceptions=True)

            results = asyncio.run(run_submits())

        # No exceptions
        exceptions = [r for r in results if isinstance(r, Exception)]
        self.assertEqual(exceptions, [], f"Submit raised exceptions: {exceptions}")

        # All returned successfully with pending status
        for r in results:
            self.assertEqual(r["status"], "pending")
            self.assertIn("job_id", r)

        # All 5 jobs exist in DB
        jobs = self.db.list_jobs()
        self.assertEqual(len(jobs), 5, f"Expected 5 jobs, got {len(jobs)}")

        # All job IDs are unique
        job_ids = [r["job_id"] for r in results]
        self.assertEqual(len(set(job_ids)), 5, f"Duplicate job IDs: {job_ids}")

    def test_duplicate_url_submit_returns_existing(self):
        """Submitting the same URL twice should return existing job, not create duplicate."""
        from app.web.services.job_service import JobService
        from app.web.services.event_bus import EventBus

        bus = EventBus()
        svc = JobService(self.cfg, self.db)
        url = "https://t.me/c/3000/1"

        with patch.object(svc, '_run_pipeline', side_effect=lambda *a: None), \
             patch('app.web.services.job_service.event_bus', bus):

            async def run():
                r1 = await svc.submit(url)
                # Mark as done to test idempotency path
                self.db.update_job_status(r1["job_id"], "done")
                r2 = await svc.submit(url)
                return r1, r2

            r1, r2 = asyncio.run(run())

        self.assertEqual(r1["job_id"], r2["job_id"])
        self.assertEqual(r2["status"], "done")
        self.assertEqual(r2["message"], "Already processed")

        # Only 1 job in DB
        jobs = self.db.list_jobs()
        self.assertEqual(len(jobs), 1)

    def test_concurrent_submit_with_read_write_mix(self):
        """Concurrent submits + list_jobs reads must not raise."""
        from app.web.services.job_service import JobService
        from app.web.services.event_bus import EventBus

        bus = EventBus()
        svc = JobService(self.cfg, self.db)
        errors = []
        lock = threading.Lock()

        def submit_in_thread(i):
            try:
                url = f"https://t.me/c/{4000+i}/{i+1}"
                loop = asyncio.new_event_loop()
                with patch.object(svc, '_run_pipeline', side_effect=lambda *a: None), \
                     patch('app.web.services.job_service.event_bus', bus):
                    loop.run_until_complete(svc.submit(url))
                loop.close()
            except Exception as e:
                with lock:
                    errors.append(e)

        def read_in_thread():
            try:
                for _ in range(10):
                    svc.list_jobs()
                    time.sleep(0.005)
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=submit_in_thread, args=(i,)) for i in range(5)]
        threads.append(threading.Thread(target=read_in_thread))

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(errors, [], f"Thread errors: {errors}")
        jobs = self.db.list_jobs()
        self.assertEqual(len(jobs), 5)


class TestAsyncUtilsThreadLocal(unittest.TestCase):
    """async_utils must provide per-thread event loops."""

    def test_different_threads_get_different_loops(self):
        from app.utils.async_utils import get_loop, close_loop

        loops = []
        lock = threading.Lock()

        def get_loop_in_thread():
            loop = get_loop()
            with lock:
                loops.append(id(loop))
            close_loop()

        threads = [threading.Thread(target=get_loop_in_thread) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        # Each thread should get a different loop
        self.assertEqual(len(set(loops)), 3, f"Expected 3 unique loops, got: {loops}")


if __name__ == "__main__":
    unittest.main()
