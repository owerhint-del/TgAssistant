"""
Работа с SQLite: инициализация, CRUD для всех таблиц.

Thread-safety: одна shared connection, ВСЕ операции (read+write)
сериализованы через _lock. Это простейшая корректная модель для
single-user приложения с shared connection (check_same_thread=False).
WAL mode включён для устойчивости при аварийном завершении.
"""
import sqlite3
import threading
import uuid
import json
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, List, Dict, Any

from app.db.models import SCHEMA
from app.utils.url_parser import TelegramLink


def _new_id() -> str:
    return str(uuid.uuid4())


class Database:
    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()

    def connect(self) -> None:
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.connect()
        return self._conn

    @contextmanager
    def _read(self):
        """Thread-safe read context: acquires lock, yields conn."""
        with self._lock:
            yield self.conn

    @contextmanager
    def _write(self):
        """Thread-safe write context: acquires lock, yields conn, commits on exit."""
        with self._lock:
            yield self.conn
            self.conn.commit()

    def migrate(self) -> None:
        """Создаёт таблицы при первом запуске + миграции для существующих БД."""
        with self._lock:
            self.conn.executescript(SCHEMA)
            # Миграция: добавляем job_type если колонки нет (существующая БД)
            cols = [
                row[1] for row in
                self.conn.execute("PRAGMA table_info(jobs)").fetchall()
            ]
            if "job_type" not in cols:
                self.conn.execute(
                    "ALTER TABLE jobs ADD COLUMN job_type TEXT NOT NULL DEFAULT 'media'"
                )
            self.conn.commit()

    # ─── jobs ─────────────────────────────────────────────────

    def get_job_by_url(self, url: str) -> Optional[Dict[str, Any]]:
        with self._read() as c:
            row = c.execute(
                "SELECT * FROM jobs WHERE url = ?", (url,)
            ).fetchone()
        return dict(row) if row else None

    def get_job_by_id(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._read() as c:
            row = c.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return dict(row) if row else None

    def create_job(self, link: TelegramLink, job_type: str = "media") -> str:
        job_id = _new_id()
        with self._write() as c:
            c.execute(
                """INSERT INTO jobs (id, url, chat_id, msg_id, status, job_type, started_at)
                   VALUES (?, ?, ?, ?, 'pending', ?, datetime('now'))""",
                (job_id, link.raw_url, link.chat_id, link.msg_id, job_type),
            )
        return job_id

    # Whitelist разрешённых колонок для update (защита от SQL-инъекций)
    _ALLOWED_JOB_COLUMNS = frozenset({
        "last_error", "retry_count", "started_at", "completed_at", "job_type",
    })

    def update_job_status(self, job_id: str, status: str, **kwargs) -> None:
        fields = ["status = ?", "updated_at = datetime('now')"]
        values = [status]
        if status == "done":
            fields.append("completed_at = datetime('now')")
        for key, val in kwargs.items():
            if key not in self._ALLOWED_JOB_COLUMNS:
                raise ValueError(f"Недопустимое поле для обновления: {key!r}")
            fields.append(f"{key} = ?")
            values.append(val)
        values.append(job_id)
        with self._write() as c:
            c.execute(
                f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?",
                values,
            )

    def increment_retry(self, job_id: str) -> None:
        with self._write() as c:
            c.execute(
                "UPDATE jobs SET retry_count = retry_count + 1, updated_at = datetime('now') WHERE id = ?",
                (job_id,),
            )

    def list_jobs(self, status_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._read() as c:
            if status_filter:
                rows = c.execute(
                    "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC",
                    (status_filter,),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM jobs ORDER BY created_at DESC"
                ).fetchall()
        return [dict(r) for r in rows]

    # ─── assets ────────────────────────────────────────────────

    def save_asset(
        self,
        job_id: str,
        asset_type: str,
        temp_path: str,
        original_filename: Optional[str] = None,
        mime_type: Optional[str] = None,
        file_size_bytes: Optional[int] = None,
        duration_sec: Optional[float] = None,
    ) -> str:
        asset_id = _new_id()
        with self._write() as c:
            c.execute(
                """INSERT INTO assets
                   (id, job_id, asset_type, original_filename, mime_type,
                    temp_path, file_size_bytes, duration_sec)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (asset_id, job_id, asset_type, original_filename, mime_type,
                 temp_path, file_size_bytes, duration_sec),
            )
        return asset_id

    def mark_asset_deleted(self, job_id: str) -> None:
        with self._write() as c:
            c.execute(
                "UPDATE assets SET temp_path = NULL, deleted_at = datetime('now') WHERE job_id = ?",
                (job_id,),
            )

    def get_asset(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._read() as c:
            row = c.execute(
                "SELECT * FROM assets WHERE job_id = ? LIMIT 1", (job_id,)
            ).fetchone()
        return dict(row) if row else None

    # ─── transcripts ───────────────────────────────────────────

    def save_transcript(
        self,
        job_id: str,
        full_text: str,
        segments: list,
        language: str,
        model_used: str,
        duration_sec: Optional[float],
        word_count: int,
        unrecognized_count: int,
    ) -> str:
        tid = _new_id()
        with self._write() as c:
            c.execute(
                """INSERT INTO transcripts
                   (id, job_id, full_text, segments_json, language,
                    model_used, duration_sec, word_count, unrecognized_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (tid, job_id, full_text, json.dumps(segments, ensure_ascii=False),
                 language, model_used, duration_sec, word_count, unrecognized_count),
            )
        return tid

    def get_transcript(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._read() as c:
            row = c.execute(
                "SELECT * FROM transcripts WHERE job_id = ?", (job_id,)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["segments"] = json.loads(d["segments_json"])
        return d

    # ─── summaries ─────────────────────────────────────────────

    def save_summary(
        self,
        job_id: str,
        content: str,
        model_used: str,
        prompt_tokens: int,
        completion_tokens: int,
        chunks_count: int = 1,
        summary_language: str = "ru",
    ) -> str:
        sid = _new_id()
        with self._write() as c:
            c.execute(
                """INSERT INTO summaries
                   (id, job_id, content, model_used, prompt_tokens,
                    completion_tokens, chunks_count, summary_language)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (sid, job_id, content, model_used, prompt_tokens,
                 completion_tokens, chunks_count, summary_language),
            )
        return sid

    def get_summary(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._read() as c:
            row = c.execute(
                "SELECT * FROM summaries WHERE job_id = ?", (job_id,)
            ).fetchone()
        return dict(row) if row else None

    # ─── exports ───────────────────────────────────────────────

    def save_export(
        self,
        job_id: str,
        export_type: str,
        file_path: str,
        file_size_bytes: Optional[int] = None,
        page_count: Optional[int] = None,
    ) -> str:
        eid = _new_id()
        with self._write() as c:
            c.execute(
                """INSERT INTO exports (id, job_id, export_type, file_path, file_size_bytes, page_count)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (eid, job_id, export_type, file_path, file_size_bytes, page_count),
            )
        return eid

    def get_exports(self, job_id: str) -> List[Dict[str, Any]]:
        with self._read() as c:
            rows = c.execute(
                "SELECT * FROM exports WHERE job_id = ? ORDER BY created_at", (job_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ─── errors ────────────────────────────────────────────────

    def log_error(
        self,
        error_type: str,
        error_message: str,
        job_id: Optional[str] = None,
        step: Optional[str] = None,
        exc: Optional[Exception] = None,
    ) -> None:
        stack = traceback.format_exc() if exc else None
        with self._write() as c:
            c.execute(
                """INSERT INTO errors (id, job_id, step, error_type, error_message, stack_trace)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (_new_id(), job_id, step, error_type, error_message, stack),
            )
