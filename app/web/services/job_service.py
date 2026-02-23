"""
Bridge between web API and the existing pipeline.
Запускает обработку в фоновом потоке, публикует события через EventBus.
"""
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Callable

from app.config import Config
from app.db.database import Database
from app.utils.url_parser import parse_url, TelegramLink
from app.utils.async_utils import safe_disconnect
from app.auth.session_manager import make_client
from app.queue.worker import Worker
from app.web.services.event_bus import event_bus

logger = logging.getLogger("tgassistant.web.job_service")


def _notify(job_id: str, status: str, **extra):
    """Publish a job status event to SSE subscribers."""
    event_bus.publish({
        "type": "job_update",
        "job_id": job_id,
        "status": status,
        **extra,
    })


class JobService:
    """Manages job submission and execution for the web interface."""

    def __init__(self, cfg: Config, db: Database):
        self.cfg = cfg
        self.db = db
        # Single-worker pool: jobs queue up and execute one at a time
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pipeline")

    async def submit(self, url: str, from_start: bool = False) -> dict:
        """
        Submit a URL for processing. Returns job info dict.
        Raises ValueError for invalid URLs or duplicate jobs.
        """
        # Parse and validate
        link = parse_url(url)

        # Idempotency check
        existing = self.db.get_job_by_url(url)
        if existing and not from_start:
            status = existing["status"]
            if status == "done":
                return {"job_id": existing["id"], "status": "done", "message": "Already processed"}
            elif status in ("downloading", "transcribing", "exporting", "collecting"):
                return {"job_id": existing["id"], "status": status, "message": "Already in progress"}
            elif status == "error":
                # Auto-retry on resubmit
                job_id = existing["id"]
                self.db.update_job_status(job_id, "pending", retry_count=0)
                loop = asyncio.get_running_loop()
                loop.run_in_executor(
                    self._executor, self._run_pipeline, job_id, link, False,
                )
                _notify(job_id, "pending")
                return {"job_id": job_id, "status": "pending", "message": "Retrying failed job"}

        # Create or reset job
        if existing and from_start:
            job_id = existing["id"]
            self.db.update_job_status(job_id, "pending")
        else:
            job_id = self.db.create_job(link)

        # Run pipeline in background thread
        loop = asyncio.get_running_loop()
        loop.run_in_executor(
            self._executor, self._run_pipeline, job_id, link, from_start,
        )

        _notify(job_id, "pending")
        return {"job_id": job_id, "status": "pending", "message": "Processing started"}

    async def retry(self, job_id: str, from_start: bool = False) -> dict:
        """Retry a failed job. Returns job info dict."""
        job = self.db.get_job_by_id(job_id)
        if not job:
            raise ValueError(f"Job not found: {job_id}")

        link = parse_url(job["url"])
        self.db.update_job_status(job_id, "pending", retry_count=0)

        loop = asyncio.get_running_loop()
        loop.run_in_executor(
            self._executor, self._run_pipeline, job_id, link, from_start,
        )

        _notify(job_id, "pending")
        return {"job_id": job_id, "status": "pending", "message": "Retry started"}

    def _run_pipeline(self, job_id: str, link: TelegramLink, from_start: bool):
        """
        Execute the pipeline synchronously in a thread pool.
        Uses per-thread event loop via async_utils (thread-local, no global race).
        GUARANTEE: always emits a terminal event (done or error) via _notify.
        """
        from app.utils.async_utils import run_sync, close_loop

        terminal_sent = False

        try:
            client = make_client(self.cfg)
            run_sync(client.connect())

            # Check auth
            if not run_sync(client.is_user_authorized()):
                self.db.update_job_status(job_id, "error", last_error="Telegram not authorized")
                _notify(job_id, "error", error="Telegram not authorized")
                terminal_sent = True
                safe_disconnect(client)
                return

            worker = Worker(self.cfg, self.db, progress_cb=_notify)
            pdf_paths = worker.process(job_id, link, client, from_start=from_start)

            safe_disconnect(client)

            if pdf_paths:
                _notify(job_id, "done")
                terminal_sent = True
            else:
                # Worker returned None — job is in error state in DB
                job = self.db.get_job_by_id(job_id)
                error_msg = (job or {}).get("last_error", "Unknown error")
                _notify(job_id, "error", error=error_msg)
                terminal_sent = True

        except Exception as e:
            logger.exception("Pipeline failed for job %s", job_id)
            self.db.update_job_status(job_id, "error", last_error=str(e))
            _notify(job_id, "error", error=str(e))
            terminal_sent = True
        finally:
            # Safety net: if somehow no terminal event was sent, send one now
            if not terminal_sent:
                _notify(job_id, "error", error="Pipeline finished without terminal status")
            close_loop()

    def list_jobs(self, status_filter: Optional[str] = None) -> list:
        """List all jobs with their exports."""
        jobs = self.db.list_jobs(status_filter)
        result = []
        for job in jobs:
            exports = self.db.get_exports(job["id"]) if job["status"] == "done" else []
            result.append({**job, "exports": exports})
        return result

    def get_job_detail(self, job_id: str) -> Optional[dict]:
        """Get full job details including exports."""
        job = self.db.get_job_by_id(job_id)
        if not job:
            return None
        exports = self.db.get_exports(job_id)
        return {**job, "exports": exports}
