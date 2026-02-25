"""
BatchRunner: последовательная обработка ParsedNote.

Ключевые принципы:
  - Переиспользует Worker.process() — ноль дупликации пайплайна
  - Один Telethon-клиент на весь батч (создаётся перед циклом)
  - Последовательная обработка (безопасно для Whisper + TG rate limits)
  - Изоляция ошибок: сбой одного элемента не ломает весь батч
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Callable

from app.config import Config
from app.db.database import Database
from app.queue.worker import Worker
from app.utils.url_parser import ExternalLink, TelegramLink
from app.batch.note_parser import ParsedNote, NoteEntry
from app.batch import index_builder

logger = logging.getLogger("tgassistant.batch.runner")


@dataclass
class BatchItemResult:
    entry: NoteEntry
    index: int              # 1-based
    success: bool = False
    job_id: Optional[str] = None
    result: Optional[dict] = None
    error: Optional[str] = None
    artifact_dir: Optional[str] = None


@dataclass
class BatchResult:
    topic: str
    topic_dir: Optional[str] = None
    items: list[BatchItemResult] = field(default_factory=list)
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: Optional[datetime] = None

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def succeeded(self) -> int:
        return sum(1 for i in self.items if i.success)

    @property
    def failed(self) -> int:
        return sum(1 for i in self.items if not i.success)


class BatchRunner:
    """Последовательно обрабатывает записи из ParsedNote."""

    def __init__(
        self,
        cfg: Config,
        db: Database,
        progress_cb: Optional[Callable] = None,
    ):
        self.cfg = cfg
        self.db = db
        self._progress_cb = progress_cb

    def run(
        self,
        note: ParsedNote,
        from_start: bool = False,
        use_symlinks: bool = True,
    ) -> BatchResult:
        """
        Обрабатывает все записи из заметки последовательно.

        Args:
            note: Разобранная заметка.
            from_start: Переобработать даже завершённые задачи.
            use_symlinks: Использовать симлинки (True) или копировать (False).

        Returns:
            BatchResult с результатами по каждому элементу.
        """
        result = BatchResult(topic=note.topic)
        valid_entries = [e for e in note.entries if e.link is not None]

        if not valid_entries:
            result.finished_at = datetime.now()
            return result

        # Определяем нужен ли Telegram-клиент
        has_tg = any(isinstance(e.link, TelegramLink) for e in valid_entries)
        client = None

        try:
            if has_tg:
                client = self._get_telegram_client()

            worker = Worker(self.cfg, self.db, progress_cb=self._progress_cb)

            for idx, entry in enumerate(valid_entries, start=1):
                item_result = self._process_entry(
                    worker=worker,
                    entry=entry,
                    index=idx,
                    client=client,
                    from_start=from_start,
                )
                result.items.append(item_result)

                # Прогресс-колбек
                if self._progress_cb:
                    try:
                        self._progress_cb(
                            None,
                            f"batch:{idx}/{len(valid_entries)}",
                        )
                    except Exception:
                        pass

        finally:
            if client:
                self._disconnect_client(client)
            result.finished_at = datetime.now()

        # Строим индекс если есть хоть один результат
        if result.items:
            try:
                topic_dir = index_builder.build(
                    topic=note.topic,
                    items=result.items,
                    output_dir=self.cfg.output_dir,
                    use_symlinks=use_symlinks,
                    started_at=result.started_at,
                    finished_at=result.finished_at,
                )
                result.topic_dir = topic_dir
            except Exception as e:
                logger.error("Failed to build index: %s", e)

        return result

    def _process_entry(
        self,
        worker: Worker,
        entry: NoteEntry,
        index: int,
        client,
        from_start: bool,
    ) -> BatchItemResult:
        """Обрабатывает одну запись с изоляцией ошибок."""
        item = BatchItemResult(entry=entry, index=index)

        try:
            url = entry.url
            link = entry.link

            # Идемпотентность: проверяем в БД
            existing = self.db.get_job_by_url(url)

            if existing and not from_start:
                if existing["status"] == "done":
                    # Уже обработано — достаём артефакт
                    item.success = True
                    item.job_id = existing["id"]
                    item.artifact_dir = self._find_artifact_dir(existing["id"])
                    logger.info("Batch [%d]: already done — %s", index, url)
                    return item
                elif existing["status"] in ("downloading", "transcribing", "exporting",
                                             "collecting", "analyzing", "saving"):
                    item.error = f"Already processing (status: {existing['status']})"
                    item.job_id = existing["id"]
                    return item
                elif existing["status"] == "error":
                    # Авто-retry
                    job_id = existing["id"]
                    self.db.update_job_status(job_id, "pending", retry_count=0)
                elif existing["status"] == "pending":
                    job_id = existing["id"]
                else:
                    job_id = existing["id"]
            elif existing and from_start:
                job_id = existing["id"]
                self.db.update_job_status(job_id, "pending")
            else:
                # Новая задача
                is_external = isinstance(link, ExternalLink)
                if is_external:
                    job_id = self.db.create_external_job(link)
                else:
                    job_id = self.db.create_job(link)

            item.job_id = job_id

            # Запуск обработки
            logger.info("Batch [%d]: processing %s", index, url)
            process_result = worker.process(
                job_id=job_id,
                link=link,
                client=client if isinstance(link, TelegramLink) else None,
                from_start=from_start,
            )

            if process_result:
                item.success = True
                item.result = process_result
                item.artifact_dir = (
                    process_result.get("collected_dir")
                    or process_result.get("wiki_dir")
                )
            else:
                job = self.db.get_job_by_id(job_id)
                item.error = (job or {}).get("last_error", "Unknown error")

        except Exception as e:
            logger.exception("Batch [%d]: unexpected error for %s", index, entry.url)
            item.error = str(e)

        return item

    def _find_artifact_dir(self, job_id: str) -> Optional[str]:
        """Ищет путь к артефактам по экспортам в БД."""
        exports = self.db.get_exports(job_id)
        for exp in exports:
            if exp["export_type"] in ("collected", "ingest_wiki"):
                return exp["file_path"]
        return None

    def _get_telegram_client(self):
        """Создаёт и подключает Telegram-клиент."""
        from app.auth.session_manager import get_authorized_client
        from app.utils.async_utils import run_sync

        client = get_authorized_client(self.cfg)
        run_sync(client.connect())
        return client

    def _disconnect_client(self, client):
        """Безопасно отключает Telegram-клиент."""
        from app.utils.async_utils import safe_disconnect, close_loop
        safe_disconnect(client)
        close_loop()
