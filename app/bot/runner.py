"""
BotPipelineRunner: запуск пайплайна из Telegram-бота.

Зеркалит логику JobService._run_pipeline() из веб-UI,
но результаты отправляет обратно в Telegram-чат.
"""
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from aiogram import Bot
from aiogram.types import BufferedInputFile

from app.config import Config
from app.db.database import Database
from app.utils.url_parser import ParsedLink, TelegramLink, ExternalLink
from app.auth.session_manager import make_client
from app.queue.worker import Worker
from app.bot.progress import BotProgressCallback
from app.bot.messages import (
    MSG_ERROR,
    MSG_RESULT_COLLECTED,
    MSG_RESULT_PDF,
    MSG_RESULT_TRANSCRIPT,
    MSG_RESULT_WIKI,
    MSG_TG_NOT_AUTHORIZED,
    MSG_BATCH_PROGRESS,
    MSG_BATCH_COMPLETE,
)

logger = logging.getLogger("tgassistant.bot.runner")

# Максимальная длина сообщения в Telegram
TG_MAX_TEXT_LENGTH = 4000


class BotPipelineRunner:
    """Запускает пайплайн в фоновом потоке, отправляет результат в чат."""

    def __init__(self, cfg: Config, db: Database, bot: Bot):
        self.cfg = cfg
        self.db = db
        self.bot = bot
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bot-pipeline")

    def submit(
        self,
        job_id: str,
        link: ParsedLink,
        chat_id: int,
        status_message_id: int,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """Запускает пайплайн в фоне. Не блокирует."""
        loop.run_in_executor(
            self._executor,
            self._run_pipeline,
            job_id,
            link,
            chat_id,
            status_message_id,
            loop,
        )

    def submit_batch(
        self,
        note,  # ParsedNote
        chat_id: int,
        status_message_id: int,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """Запускает пакетную обработку в фоне. Не блокирует."""
        loop.run_in_executor(
            self._executor,
            self._run_batch_pipeline,
            note,
            chat_id,
            status_message_id,
            loop,
        )

    def _run_pipeline(
        self,
        job_id: str,
        link: ParsedLink,
        chat_id: int,
        status_message_id: int,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """Синхронное выполнение пайплайна в фоновом потоке."""
        from app.utils.async_utils import run_sync, safe_disconnect, close_loop

        is_external = isinstance(link, ExternalLink)

        progress_cb = BotProgressCallback(
            bot=self.bot,
            chat_id=chat_id,
            message_id=status_message_id,
            loop=loop,
        )

        try:
            worker = Worker(self.cfg, self.db, progress_cb=progress_cb)

            if is_external:
                result = worker.process(job_id, link, client=None)
            else:
                client = make_client(self.cfg)
                run_sync(client.connect())

                if not run_sync(client.is_user_authorized()):
                    self.db.update_job_status(job_id, "error", last_error="Telegram not authorized")
                    self._send_sync(loop, chat_id, MSG_TG_NOT_AUTHORIZED)
                    self._edit_sync(loop, chat_id, status_message_id, "❌ " + MSG_TG_NOT_AUTHORIZED)
                    safe_disconnect(client)
                    return

                result = worker.process(job_id, link, client)
                safe_disconnect(client)

            if result:
                self._handle_result(loop, chat_id, status_message_id, job_id, result)
            else:
                job = self.db.get_job_by_id(job_id)
                error_msg = (job or {}).get("last_error", "Неизвестная ошибка")
                self._edit_sync(loop, chat_id, status_message_id, MSG_ERROR.format(error=error_msg))

        except Exception as e:
            logger.exception("Pipeline failed for job %s", job_id)
            self.db.update_job_status(job_id, "error", last_error=str(e))
            self._edit_sync(loop, chat_id, status_message_id, MSG_ERROR.format(error=str(e)))
        finally:
            close_loop()

    def _run_batch_pipeline(
        self,
        note,  # ParsedNote
        chat_id: int,
        status_message_id: int,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """Синхронное выполнение пакетной обработки в фоновом потоке."""
        from app.batch.batch_runner import BatchRunner

        current_idx = [0]
        total = note.valid_count

        def batch_progress(job_id, status):
            """Обновляет статусное сообщение в Telegram по ходу батча."""
            if status and status.startswith("batch:"):
                parts = status.replace("batch:", "").split("/")
                if len(parts) == 2:
                    current_idx[0] = int(parts[0])
                    self._edit_sync(
                        loop, chat_id, status_message_id,
                        MSG_BATCH_PROGRESS.format(
                            current=current_idx[0], total=total,
                            url=f"обработка {current_idx[0]}/{total}...",
                        ),
                    )

        try:
            runner = BatchRunner(self.cfg, self.db, progress_cb=batch_progress)
            result = runner.run(note)

            # Формируем итоговое сообщение
            errors_text = ""
            if result.failed > 0:
                error_lines = []
                for item in result.items:
                    if not item.success:
                        error_lines.append(f"  ✗ {item.entry.url}: {item.error}")
                errors_text = "Ошибки:\n" + "\n".join(error_lines) + "\n\n"

            self._edit_sync(
                loop, chat_id, status_message_id,
                MSG_BATCH_COMPLETE.format(
                    topic=result.topic,
                    succeeded=result.succeeded,
                    total=result.total,
                    errors=errors_text,
                    topic_dir=result.topic_dir or "—",
                ),
            )

        except Exception as e:
            logger.exception("Batch pipeline failed")
            self._edit_sync(
                loop, chat_id, status_message_id,
                MSG_ERROR.format(error=str(e)),
            )

    def _handle_result(
        self,
        loop: asyncio.AbstractEventLoop,
        chat_id: int,
        status_message_id: int,
        job_id: str,
        result: dict,
    ) -> None:
        """Обрабатывает результат пайплайна и отправляет в чат."""
        # Проверяем наличие транскрипта в БД
        transcript = self.db.get_transcript(job_id)

        if transcript and transcript.get("full_text"):
            text = transcript["full_text"]
            self._edit_sync(loop, chat_id, status_message_id, MSG_RESULT_TRANSCRIPT)

            if len(text) <= TG_MAX_TEXT_LENGTH:
                self._send_sync(loop, chat_id, text)
            else:
                # Длинный транскрипт → файл .txt
                file_data = text.encode("utf-8")
                doc = BufferedInputFile(file_data, filename="transcript.txt")
                self._send_document_sync(loop, chat_id, doc, caption="Полная транскрипция")
        elif "collected_dir" in result:
            self._edit_sync(
                loop, chat_id, status_message_id,
                MSG_RESULT_COLLECTED.format(path=result["collected_dir"]),
            )
        elif "wiki_dir" in result:
            self._edit_sync(
                loop, chat_id, status_message_id,
                MSG_RESULT_WIKI.format(path=result["wiki_dir"]),
            )
        else:
            # PDF results
            paths = "\n".join(f"→ {p}" for p in result.values())
            self._edit_sync(
                loop, chat_id, status_message_id,
                MSG_RESULT_PDF.format(path=paths),
            )

    # ─── Thread-safe helpers ────────────────────────────────

    def _send_sync(self, loop: asyncio.AbstractEventLoop, chat_id: int, text: str) -> None:
        """Отправляет сообщение из фонового потока."""
        try:
            future = asyncio.run_coroutine_threadsafe(
                self.bot.send_message(chat_id=chat_id, text=text),
                loop,
            )
            future.result(timeout=30)
        except Exception as e:
            logger.error("Failed to send message: %s", e)

    def _edit_sync(
        self, loop: asyncio.AbstractEventLoop, chat_id: int, message_id: int, text: str,
    ) -> None:
        """Редактирует сообщение из фонового потока."""
        try:
            future = asyncio.run_coroutine_threadsafe(
                self.bot.edit_message_text(
                    text=text, chat_id=chat_id, message_id=message_id,
                ),
                loop,
            )
            future.result(timeout=30)
        except Exception as e:
            logger.error("Failed to edit message: %s", e)

    def _send_document_sync(
        self,
        loop: asyncio.AbstractEventLoop,
        chat_id: int,
        document: BufferedInputFile,
        caption: str = "",
    ) -> None:
        """Отправляет документ из фонового потока."""
        try:
            future = asyncio.run_coroutine_threadsafe(
                self.bot.send_document(chat_id=chat_id, document=document, caption=caption),
                loop,
            )
            future.result(timeout=60)
        except Exception as e:
            logger.error("Failed to send document: %s", e)
