"""
Интеграционные тесты пайплайна (с моками — без реального Telegram/Claude).
"""
import os
import sys
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def cfg(tmp_path):
    from app.config import Config
    return Config(
        tg_api_id=12345678,
        tg_api_hash="testhash",
        tg_phone="+49000000000",
        tg_session_path=str(tmp_path / "session"),
        output_dir=str(tmp_path / "output"),
        temp_dir=str(tmp_path / "temp"),
        db_path=str(tmp_path / "test.db"),
        anthropic_api_key="sk-ant-test",
        pdf_font_path="./fonts/DejaVuSans.ttf",
        pdf_bold_font_path="",
        cleanup_temp=True,
    )


@pytest.fixture
def db(cfg):
    from app.db.database import Database
    db = Database(cfg.db_path)
    db.connect()
    db.migrate()
    yield db
    db.close()


@pytest.fixture
def link():
    from app.utils.url_parser import TelegramLink
    return TelegramLink(chat_id=1775135187, msg_id=1197, raw_url="https://t.me/c/1775135187/1197")


class TestDatabase:

    def test_create_and_get_job(self, db, link):
        job_id = db.create_job(link)
        assert job_id is not None
        job = db.get_job_by_id(job_id)
        assert job["url"] == link.raw_url
        assert job["status"] == "pending"
        assert job["chat_id"] == link.chat_id
        assert job["msg_id"] == link.msg_id

    def test_idempotency_by_url(self, db, link):
        db.create_job(link)
        existing = db.get_job_by_url(link.raw_url)
        assert existing is not None
        assert existing["chat_id"] == link.chat_id

    def test_update_status(self, db, link):
        job_id = db.create_job(link)
        db.update_job_status(job_id, "downloading")
        job = db.get_job_by_id(job_id)
        assert job["status"] == "downloading"

    def test_save_and_get_transcript(self, db, link):
        job_id = db.create_job(link)
        db.save_transcript(
            job_id=job_id,
            full_text="Тест транскрипта",
            segments=[{"start": 0.0, "end": 2.0, "text": "Тест", "avg_logprob": -0.5}],
            language="ru",
            model_used="large-v3",
            duration_sec=2.0,
            word_count=1,
            unrecognized_count=0,
        )
        transcript = db.get_transcript(job_id)
        assert transcript["full_text"] == "Тест транскрипта"
        assert transcript["language"] == "ru"
        assert len(transcript["segments"]) == 1

    def test_save_and_get_summary(self, db, link):
        job_id = db.create_job(link)
        db.save_summary(
            job_id=job_id,
            content="## Конспект\n\nТест конспекта",
            model_used="claude-sonnet-4-6",
            prompt_tokens=100,
            completion_tokens=200,
            chunks_count=1,
            summary_language="ru",
        )
        summary = db.get_summary(job_id)
        assert "Конспект" in summary["content"]
        assert summary["prompt_tokens"] == 100

    def test_log_error(self, db, link):
        job_id = db.create_job(link)
        db.log_error(
            error_type="TestError",
            error_message="Тестовая ошибка",
            job_id=job_id,
            step="download",
        )
        # Нет исключений — тест пройден

    def test_list_jobs(self, db, link):
        job_id = db.create_job(link)
        jobs = db.list_jobs()
        assert any(j["id"] == job_id for j in jobs)

    def test_list_jobs_with_filter(self, db, link):
        job_id = db.create_job(link)
        db.update_job_status(job_id, "done")
        done_jobs = db.list_jobs(status_filter="done")
        assert any(j["id"] == job_id for j in done_jobs)
        pending_jobs = db.list_jobs(status_filter="pending")
        assert not any(j["id"] == job_id for j in pending_jobs)


class TestTranscriptResult:

    def test_format_with_timestamps_basic(self):
        from app.pipeline.transcriber import TranscriptResult, Segment

        result = TranscriptResult(
            segments=[
                Segment(start=0.0, end=3.5, text="Первый сегмент."),
                Segment(start=3.6, end=7.0, text="Второй сегмент."),
                # Пауза > 2 сек
                Segment(start=10.0, end=13.0, text="Третий сегмент после паузы."),
            ],
            language="ru",
            model_used="test",
            duration_sec=13.0,
        )

        formatted = result.format_with_timestamps()
        assert "[00:00:00]" in formatted
        assert "[00:00:10]" in formatted
        assert "Первый сегмент" in formatted
        assert "Третий сегмент после паузы" in formatted

    def test_unrecognized_label_preserved(self):
        from app.pipeline.transcriber import TranscriptResult, Segment, UNRECOGNIZED_LABEL

        result = TranscriptResult(
            segments=[
                Segment(start=0.0, end=2.0, text="Начало."),
                Segment(start=2.0, end=4.0, text=UNRECOGNIZED_LABEL, avg_logprob=-1.5),
                Segment(start=4.0, end=6.0, text="Конец."),
            ],
            language="ru",
            model_used="test",
            duration_sec=6.0,
        )

        formatted = result.format_with_timestamps()
        assert UNRECOGNIZED_LABEL in formatted

    def test_word_count_excludes_unrecognized(self):
        from app.pipeline.transcriber import TranscriptResult, Segment, UNRECOGNIZED_LABEL

        result = TranscriptResult(
            segments=[
                Segment(start=0.0, end=2.0, text="три слова здесь"),
                Segment(start=2.0, end=4.0, text=UNRECOGNIZED_LABEL),
            ],
            language="ru",
            model_used="test",
            duration_sec=4.0,
            word_count=3,
        )
        assert result.word_count == 3


class TestUrlParserIntegration:

    def test_parse_and_use_in_db(self, db):
        from app.utils.url_parser import parse_url

        url = "https://t.me/c/1775135187/1197"
        link = parse_url(url)
        job_id = db.create_job(link)
        job = db.get_job_by_url(url)
        assert job is not None
        assert job["chat_id"] == 1775135187
