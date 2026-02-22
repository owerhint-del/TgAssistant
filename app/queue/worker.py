"""
Worker: обрабатывает задачу с retry-логикой.
"""
import logging
import time
from typing import Optional

from telethon import TelegramClient

from app.config import Config
from app.db.database import Database
from app.pipeline.orchestrator import Orchestrator, PipelineError
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
    def __init__(self, cfg: Config, db: Database):
        self.cfg = cfg
        self.db = db
        self.orchestrator = Orchestrator(cfg, db)

    def process(
        self,
        job_id: str,
        link: TelegramLink,
        client: TelegramClient,
        from_start: bool = False,
    ) -> Optional[dict]:
        """
        Обрабатывает задачу с retry при временных ошибках.

        Returns:
            dict с путями к PDF или None при неустранимой ошибке.
        """
        max_attempts = self.cfg.max_retries
        backoff = self.cfg.retry_backoff_sec

        for attempt in range(1, max_attempts + 1):
            try:
                logger.info(
                    "Запуск задачи %s (попытка %d/%d)",
                    job_id, attempt, max_attempts,
                )
                pdf_paths = self.orchestrator.run(
                    job_id=job_id,
                    link=link,
                    client=client,
                    from_start=from_start,
                )
                return pdf_paths

            except NON_RETRYABLE as e:
                # Ошибки без смысла повторять
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

            except PipelineError as e:
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
                # Неожиданные ошибки
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
