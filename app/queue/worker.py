"""
Worker: обрабатывает задачу с retry-логикой.
Маршрутизация: классифицирует сообщение и направляет к нужному оркестратору.
"""
import logging
import time
from typing import Optional, Callable

from telethon import TelegramClient

from app.config import Config
from app.db.database import Database
from app.pipeline.orchestrator import Orchestrator, PipelineError
from app.pipeline.ingest_orchestrator import IngestOrchestrator, IngestError
from app.pipeline.classifier import classify, MessageType
from app.pipeline.downloader import DownloadError, AccessDeniedError, MediaNotFoundError, UnsupportedMediaError, MediaLimitExceededError
from app.utils.url_parser import TelegramLink

logger = logging.getLogger("tgassistant.worker")

# Ошибки, при которых retry не имеет смысла
NON_RETRYABLE = (
    AccessDeniedError,
    MediaNotFoundError,
    UnsupportedMediaError,
    MediaLimitExceededError,
)


class Worker:
    def __init__(self, cfg: Config, db: Database, progress_cb: Optional[Callable] = None):
        self.cfg = cfg
        self.db = db
        self._progress_cb = progress_cb
        self.orchestrator = Orchestrator(cfg, db, progress_cb=progress_cb)
        self.ingest_orchestrator = IngestOrchestrator(cfg, db, progress_cb=progress_cb)

    def _determine_job_type(self, job_id: str, link: TelegramLink, client: TelegramClient) -> str:
        """
        Определяет тип задачи: проверяет БД (resume), иначе классифицирует сообщение.
        Записывает job_type в БД.
        """
        job = self.db.get_job_by_id(job_id)
        existing_type = job.get("job_type") if job else None

        # Если тип уже определён и это не дефолтный 'media' (resume)
        if existing_type and existing_type != "media":
            return existing_type

        # Если тип 'media' — он мог быть выставлен по умолчанию, классифицируем
        try:
            msg_type = classify(client, link)
        except ValueError as e:
            raise MediaNotFoundError(str(e))

        if msg_type == MessageType.AUDIO_VIDEO:
            job_type = "media"
        else:
            job_type = "ingest"

        # Обновляем тип в БД
        self.db.update_job_status(job_id, job["status"], job_type=job_type)
        logger.info("Тип задачи %s: %s (классификация: %s)", job_id, job_type, msg_type.value)
        return job_type

    def process(
        self,
        job_id: str,
        link: TelegramLink,
        client: TelegramClient,
        from_start: bool = False,
    ) -> Optional[dict]:
        """
        Обрабатывает задачу с retry при временных ошибках.
        Маршрутизирует к Orchestrator (media) или IngestOrchestrator (ingest).

        Returns:
            dict с результатами или None при неустранимой ошибке.
        """
        max_attempts = self.cfg.max_retries
        backoff = self.cfg.retry_backoff_sec

        for attempt in range(1, max_attempts + 1):
            try:
                logger.info(
                    "Запуск задачи %s (попытка %d/%d)",
                    job_id, attempt, max_attempts,
                )

                # Определяем тип задачи (media или ingest)
                job_type = self._determine_job_type(job_id, link, client)

                if job_type == "ingest":
                    result = self.ingest_orchestrator.run(
                        job_id=job_id,
                        link=link,
                        client=client,
                        from_start=from_start,
                    )
                else:
                    result = self.orchestrator.run(
                        job_id=job_id,
                        link=link,
                        client=client,
                        from_start=from_start,
                    )
                return result

            except NON_RETRYABLE as e:
                error_msg = str(e)
                logger.error("Неустранимая ошибка: %s", error_msg)
                self.db.log_error(
                    error_type=type(e).__name__,
                    error_message=error_msg,
                    job_id=job_id,
                    step=getattr(e, "step", "download"),
                )
                self.db.update_job_status(
                    job_id, "error", last_error=error_msg
                )
                return None

            except (PipelineError, IngestError) as e:
                error_msg = str(e)
                self.db.increment_retry(job_id)
                self.db.log_error(
                    error_type=type(e).__name__,
                    error_message=error_msg,
                    job_id=job_id,
                    step=e.step,
                    exc=e,
                )

                if attempt >= max_attempts:
                    logger.error(
                        "Задача %s провалена после %d попыток. Последняя ошибка: %s",
                        job_id, max_attempts, error_msg,
                    )
                    self.db.update_job_status(
                        job_id, "error", last_error=error_msg
                    )
                    return None

                wait = backoff * (2 ** (attempt - 1))
                logger.warning(
                    "Попытка %d/%d не удалась [%s]: %s\n  Повтор через %.0f сек...",
                    attempt, max_attempts, e.step, error_msg, wait,
                )
                time.sleep(wait)

            except Exception as e:
                error_msg = str(e)
                self.db.increment_retry(job_id)
                self.db.log_error(
                    error_type=type(e).__name__,
                    error_message=error_msg,
                    job_id=job_id,
                    step="unknown",
                    exc=e,
                )

                if attempt >= max_attempts:
                    logger.error(
                        "Неожиданная ошибка в задаче %s: %s",
                        job_id, error_msg,
                    )
                    self.db.update_job_status(
                        job_id, "error", last_error=error_msg
                    )
                    return None

                wait = backoff * (2 ** (attempt - 1))
                logger.warning(
                    "Неожиданная ошибка (попытка %d/%d): %s. Повтор через %.0f сек...",
                    attempt, max_attempts, error_msg, wait,
                )
                time.sleep(wait)

        return None
