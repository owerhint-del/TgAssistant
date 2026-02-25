"""
Тесты external collector пайплайна: скачивание видео через yt-dlp + транскрипция.
"""
import json
import os
import sys
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.url_parser import ExternalLink


@pytest.fixture
def cfg(tmp_path):
    from app.config import Config
    return Config(
        tg_phone="+49000000000",
        tg_session_path=str(tmp_path / "session"),
        output_dir=str(tmp_path / "output"),
        temp_dir=str(tmp_path / "temp"),
        db_path=str(tmp_path / "test.db"),
        anthropic_api_key="",
        pdf_font_path="./fonts/DejaVuSans.ttf",
        pdf_bold_font_path="",
        cleanup_temp=True,
        max_file_mb=2000,
        max_duration_sec=7200,
        ytdlp_cookies_file="",
        ytdlp_format="bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",
    )


@pytest.fixture
def db(cfg):
    from app.db.database import Database
    database = Database(cfg.db_path)
    database.connect()
    database.migrate()
    yield database
    database.close()


@pytest.fixture
def youtube_link():
    return ExternalLink(source="youtube", video_id="dQw4w9WgXcQ", raw_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")


@pytest.fixture
def x_link():
    return ExternalLink(source="x", video_id="1234567890", raw_url="https://x.com/user/status/1234567890")


def _mock_yt_info(duration=120, filesize=50000000, title="Test Video", description="Test description"):
    """Мок результата yt-dlp extract_info."""
    return {
        "title": title,
        "uploader": "Test Channel",
        "channel": "Test Channel",
        "upload_date": "20250115",
        "duration": duration,
        "description": description,
        "filesize": filesize,
        "filesize_approx": filesize,
        "thumbnail": "https://example.com/thumb.jpg",
    }


# ─── Path Tests ───────────────────────────────────────────────

class TestCollectedDirPath:

    def test_youtube_path(self, cfg, db, youtube_link):
        from app.pipeline.external_collector import ExternalCollectorOrchestrator
        orch = ExternalCollectorOrchestrator(cfg, db)
        path = orch._collected_dir(youtube_link)
        parts = path.parts
        assert "collected" in parts
        assert "external" in parts
        assert "youtube" in parts
        assert "dQw4w9WgXcQ" in parts

    def test_x_path(self, cfg, db, x_link):
        from app.pipeline.external_collector import ExternalCollectorOrchestrator
        orch = ExternalCollectorOrchestrator(cfg, db)
        path = orch._collected_dir(x_link)
        assert "external" in str(path)
        assert "x" in str(path)
        assert "1234567890" in str(path)

    def test_path_format(self, cfg, db, youtube_link):
        """Путь: <output_dir>/collected/external/<source>/<video_id>/."""
        from app.pipeline.external_collector import ExternalCollectorOrchestrator
        orch = ExternalCollectorOrchestrator(cfg, db)
        path = orch._collected_dir(youtube_link)
        parts = path.parts
        ext_idx = parts.index("external")
        assert parts[ext_idx - 1] == "collected"
        assert parts[ext_idx + 1] == "youtube"
        assert parts[ext_idx + 2] == "dQw4w9WgXcQ"


# ─── Full Pipeline Tests ─────────────────────────────────────

class TestCollectYouTubeVideo:

    def test_collect_youtube_video(self, cfg, db, youtube_link):
        """Полный пайплайн: download + transcribe + meta + manifest (мок yt-dlp)."""
        from app.pipeline.external_collector import ExternalCollectorOrchestrator
        from app.pipeline.transcriber import TranscriptResult, Segment

        job_id = db.create_external_job(youtube_link)
        orch = ExternalCollectorOrchestrator(cfg, db)
        collected_dir = orch._collected_dir(youtube_link)

        # Мок yt-dlp info
        mock_info = _mock_yt_info()

        # Мок yt-dlp download — создаём файлы
        def mock_download(self_ydl, urls):
            attachments_dir = collected_dir / "attachments"
            attachments_dir.mkdir(parents=True, exist_ok=True)
            (attachments_dir / "Test Video.mp4").write_bytes(b"fake_video" * 1000)
            (attachments_dir / "Test Video.jpg").write_bytes(b"fake_thumb")

        # Мок Transcriber
        mock_transcript = TranscriptResult(
            segments=[
                Segment(start=0.0, end=5.0, text="Привет мир", avg_logprob=-0.3),
                Segment(start=5.0, end=10.0, text="Это тест видео", avg_logprob=-0.4),
            ],
            language="ru",
            model_used="large-v3",
            duration_sec=120.0,
            word_count=5,
            unrecognized_count=0,
        )

        with patch('app.pipeline.external_collector.ExternalCollectorOrchestrator._get_info', return_value=mock_info), \
             patch('app.pipeline.external_collector.ExternalCollectorOrchestrator._download_video') as mock_dl, \
             patch('app.pipeline.transcriber.Transcriber.transcribe', return_value=mock_transcript):

            # _download_video создаёт файлы и возвращает путь
            attachments_dir = collected_dir / "attachments"
            attachments_dir.mkdir(parents=True, exist_ok=True)
            video_file = attachments_dir / "Test Video.mp4"
            video_file.write_bytes(b"fake_video" * 1000)
            thumb_file = attachments_dir / "Test Video.jpg"
            thumb_file.write_bytes(b"fake_thumb")
            mock_dl.return_value = video_file

            result = orch.run(job_id, youtube_link, from_start=False)

        assert "collected_dir" in result
        collected_dir = Path(result["collected_dir"])

        # Проверяем файлы
        assert (collected_dir / "description.txt").exists()
        assert (collected_dir / "transcript.txt").exists()
        assert (collected_dir / "meta.json").exists()
        assert (collected_dir / "manifest.json").exists()
        assert (collected_dir / "attachments").exists()

        # Проверяем description.txt
        desc = (collected_dir / "description.txt").read_text(encoding="utf-8")
        assert desc == "Test description"

        # Проверяем meta.json
        meta = json.loads((collected_dir / "meta.json").read_text(encoding="utf-8"))
        assert meta["source"] == "youtube"
        assert meta["video_id"] == "dQw4w9WgXcQ"
        assert meta["title"] == "Test Video"
        assert meta["uploader"] == "Test Channel"
        assert meta["has_transcript"] is True
        assert meta["transcript_language"] == "ru"
        assert meta["transcript_word_count"] == 5

        # Проверяем manifest.json
        manifest = json.loads((collected_dir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["version"] == 1
        assert manifest["source"] == "external"
        types = [a["type"] for a in manifest["artifacts"]]
        assert "description" in types
        assert "transcript" in types
        assert "attachment" in types

        # Проверяем export в БД
        exports = db.get_exports(job_id)
        assert len(exports) == 1
        assert exports[0]["export_type"] == "collected"

        # Проверяем статус
        job = db.get_job_by_id(job_id)
        assert job["status"] == "done"


# ─── Idempotency Tests ───────────────────────────────────────

class TestExternalIdempotency:

    def test_second_run_returns_cached(self, cfg, db, youtube_link):
        """Повторный запуск без from_start → кэшированный результат."""
        from app.pipeline.external_collector import ExternalCollectorOrchestrator
        from app.pipeline.transcriber import TranscriptResult, Segment

        job_id = db.create_external_job(youtube_link)
        orch = ExternalCollectorOrchestrator(cfg, db)
        collected_dir = orch._collected_dir(youtube_link)

        mock_info = _mock_yt_info()
        mock_transcript = TranscriptResult(
            segments=[Segment(start=0.0, end=5.0, text="Тест", avg_logprob=-0.3)],
            language="ru", model_used="large-v3", duration_sec=10.0,
            word_count=1, unrecognized_count=0,
        )

        with patch('app.pipeline.external_collector.ExternalCollectorOrchestrator._get_info', return_value=mock_info), \
             patch('app.pipeline.external_collector.ExternalCollectorOrchestrator._download_video') as mock_dl, \
             patch('app.pipeline.transcriber.Transcriber.transcribe', return_value=mock_transcript):

            attachments_dir = collected_dir / "attachments"
            attachments_dir.mkdir(parents=True, exist_ok=True)
            video_file = attachments_dir / "video.mp4"
            video_file.write_bytes(b"fake" * 100)
            mock_dl.return_value = video_file

            result1 = orch.run(job_id, youtube_link, from_start=False)

        # Second run — should use cache
        result2 = orch.run(job_id, youtube_link, from_start=False)

        assert result1["collected_dir"] == result2["collected_dir"]
        exports = db.get_exports(job_id)
        assert len(exports) == 1


# ─── From Start Tests ────────────────────────────────────────

class TestExternalFromStart:

    def test_from_start_overwrites(self, cfg, db, youtube_link):
        """--from-start перезаписывает существующие данные."""
        from app.pipeline.external_collector import ExternalCollectorOrchestrator
        from app.pipeline.transcriber import TranscriptResult, Segment

        job_id = db.create_external_job(youtube_link)
        orch = ExternalCollectorOrchestrator(cfg, db)
        collected_dir = orch._collected_dir(youtube_link)

        def _setup_and_run(description):
            mock_info = _mock_yt_info(description=description)
            mock_transcript = TranscriptResult(
                segments=[Segment(start=0.0, end=5.0, text="Тест", avg_logprob=-0.3)],
                language="ru", model_used="large-v3", duration_sec=10.0,
                word_count=1, unrecognized_count=0,
            )
            with patch('app.pipeline.external_collector.ExternalCollectorOrchestrator._get_info', return_value=mock_info), \
                 patch('app.pipeline.external_collector.ExternalCollectorOrchestrator._download_video') as mock_dl, \
                 patch('app.pipeline.transcriber.Transcriber.transcribe', return_value=mock_transcript):
                attachments_dir = collected_dir / "attachments"
                attachments_dir.mkdir(parents=True, exist_ok=True)
                video_file = attachments_dir / "video.mp4"
                video_file.write_bytes(b"fake" * 100)
                mock_dl.return_value = video_file
                return orch.run(job_id, youtube_link, from_start=True)

        _setup_and_run("Original description")
        result2 = _setup_and_run("Updated description")

        desc = (Path(result2["collected_dir"]) / "description.txt").read_text(encoding="utf-8")
        assert desc == "Updated description"


# ─── Limit Tests ─────────────────────────────────────────────

class TestExternalLimits:

    def test_duration_limit(self, cfg, db, youtube_link):
        """Видео длиннее max_duration_sec → MediaLimitExceededError."""
        from app.pipeline.external_collector import ExternalCollectorOrchestrator
        from app.pipeline.downloader import MediaLimitExceededError

        cfg.max_duration_sec = 600  # 10 минут
        job_id = db.create_external_job(youtube_link)
        orch = ExternalCollectorOrchestrator(cfg, db)

        mock_info = _mock_yt_info(duration=3600)  # 1 час

        with patch('app.pipeline.external_collector.ExternalCollectorOrchestrator._get_info', return_value=mock_info):
            with pytest.raises(MediaLimitExceededError, match="слишком длинное"):
                orch.run(job_id, youtube_link)

    def test_filesize_limit(self, cfg, db, youtube_link):
        """Файл больше max_file_mb → MediaLimitExceededError."""
        from app.pipeline.external_collector import ExternalCollectorOrchestrator
        from app.pipeline.downloader import MediaLimitExceededError

        cfg.max_file_mb = 100  # 100 MB
        job_id = db.create_external_job(youtube_link)
        orch = ExternalCollectorOrchestrator(cfg, db)

        mock_info = _mock_yt_info(filesize=500 * 1024 * 1024)  # 500 MB

        with patch('app.pipeline.external_collector.ExternalCollectorOrchestrator._get_info', return_value=mock_info):
            with pytest.raises(MediaLimitExceededError, match="слишком большой"):
                orch.run(job_id, youtube_link)


# ─── Error Tests ─────────────────────────────────────────────

class TestExternalErrors:

    def test_missing_video(self, cfg, db, youtube_link):
        """yt-dlp DownloadError → ExternalCollectorError."""
        from app.pipeline.external_collector import ExternalCollectorOrchestrator, ExternalCollectorError

        job_id = db.create_external_job(youtube_link)
        orch = ExternalCollectorOrchestrator(cfg, db)

        # Симулируем yt-dlp DownloadError
        import yt_dlp
        with patch('app.pipeline.external_collector.ExternalCollectorOrchestrator._get_info',
                    side_effect=yt_dlp.utils.DownloadError("Video unavailable")):
            with pytest.raises(ExternalCollectorError, match="не найдено или недоступно"):
                orch.run(job_id, youtube_link)

    def test_age_restricted_without_cookies(self, cfg, db, youtube_link):
        """Age-restricted video без cookies → понятное сообщение."""
        from app.pipeline.external_collector import ExternalCollectorOrchestrator, ExternalCollectorError

        job_id = db.create_external_job(youtube_link)
        orch = ExternalCollectorOrchestrator(cfg, db)

        import yt_dlp
        with patch('app.pipeline.external_collector.ExternalCollectorOrchestrator._get_info',
                    side_effect=yt_dlp.utils.DownloadError("Sign in to confirm your age")):
            with pytest.raises(ExternalCollectorError, match="YTDLP_COOKIES_FILE"):
                orch.run(job_id, youtube_link)


# ─── Worker Routing Tests ────────────────────────────────────

class TestWorkerRoutesExternal:

    def test_worker_routes_external_jobs(self, cfg, db, youtube_link):
        """Worker направляет 'external' задачи к ExternalCollectorOrchestrator."""
        from app.queue.worker import Worker

        job_id = db.create_external_job(youtube_link)
        worker = Worker(cfg, db)

        mock_result = {"collected_dir": "/tmp/test"}

        with patch.object(worker.external_collector, 'run', return_value=mock_result) as mock_run:
            result = worker.process(job_id, youtube_link, client=None)

        mock_run.assert_called_once()
        assert result == mock_result


# ─── DB Tests ────────────────────────────────────────────────

class TestDbCreateExternalJob:

    def test_create_external_job(self, db, youtube_link):
        """create_external_job() с chat_id=0, msg_id=0."""
        job_id = db.create_external_job(youtube_link)
        job = db.get_job_by_id(job_id)
        assert job is not None
        assert job["url"] == youtube_link.raw_url
        assert job["chat_id"] == 0
        assert job["msg_id"] == 0
        assert job["job_type"] == "external"
        assert job["status"] == "pending"

    def test_external_job_idempotency(self, db, youtube_link):
        """Повторный create_external_job с тем же URL → UNIQUE constraint."""
        import sqlite3
        db.create_external_job(youtube_link)
        with pytest.raises(sqlite3.IntegrityError):
            db.create_external_job(youtube_link)
