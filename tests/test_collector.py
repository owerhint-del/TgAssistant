"""
Тесты collector-пайплайна: единый сбор всех данных из Telegram-сообщений.
"""
import json
import os
import sys
import pytest
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.url_parser import TelegramLink


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
def private_link():
    return TelegramLink(chat_id=1775135187, msg_id=1197, raw_url="https://t.me/c/1775135187/1197")


@pytest.fixture
def public_link():
    return TelegramLink(chat_id=0, msg_id=42, raw_url="https://t.me/durov/42", channel_username="durov")


def _make_text_message(text="Привет, это тестовое сообщение!"):
    """Мок текстового сообщения без медиа."""
    msg = MagicMock()
    msg.text = text
    msg.media = None
    msg.date = datetime(2025, 1, 15, 10, 30, 0)
    msg.forward = None
    msg.grouped_id = None
    msg.id = 1197
    return msg


def _make_photo_media():
    """Мок медиа с фото."""
    from telethon.tl.types import MessageMediaPhoto
    media = MagicMock(spec=MessageMediaPhoto)
    media.__class__ = MessageMediaPhoto
    return media


def _make_doc_media(mime_type="application/pdf", size=1024):
    """Мок медиа с документом."""
    from telethon.tl.types import MessageMediaDocument
    doc = MagicMock()
    doc.mime_type = mime_type
    doc.attributes = []
    doc.size = size
    media = MagicMock(spec=MessageMediaDocument)
    media.__class__ = MessageMediaDocument
    media.document = doc
    return media


def _make_video_media(mime_type="video/mp4", size=15000000):
    """Мок медиа с видео (имеет DocumentAttributeVideo)."""
    from telethon.tl.types import MessageMediaDocument, DocumentAttributeVideo
    attr = DocumentAttributeVideo(duration=120, w=1920, h=1080)
    doc = MagicMock()
    doc.mime_type = mime_type
    doc.attributes = [attr]
    doc.size = size
    media = MagicMock(spec=MessageMediaDocument)
    media.__class__ = MessageMediaDocument
    media.document = doc
    return media


# ─── Path Tests ───────────────────────────────────────────────

class TestCollectedDirPath:

    def test_private_channel_path(self, cfg, db, private_link):
        from app.pipeline.collector import CollectorOrchestrator
        orch = CollectorOrchestrator(cfg, db)
        path = orch._collected_dir(private_link)
        assert "collected" in str(path)
        assert str(private_link.chat_id) in str(path)
        assert str(private_link.msg_id) in str(path)

    def test_public_channel_path(self, cfg, db, public_link):
        from app.pipeline.collector import CollectorOrchestrator
        orch = CollectorOrchestrator(cfg, db)
        path = orch._collected_dir(public_link)
        assert "collected" in str(path)
        assert "durov" in str(path)
        assert str(public_link.msg_id) in str(path)

    def test_path_format(self, cfg, db, private_link):
        """Путь: <output_dir>/collected/<chat_id>/<msg_id>/."""
        from app.pipeline.collector import CollectorOrchestrator
        orch = CollectorOrchestrator(cfg, db)
        path = orch._collected_dir(private_link)
        parts = path.parts
        assert "collected" in parts
        idx = parts.index("collected")
        assert parts[idx + 1] == str(private_link.chat_id)
        assert parts[idx + 2] == str(private_link.msg_id)


# ─── Text-Only Tests ─────────────────────────────────────────

class TestCollectTextOnly:

    def test_collect_text_only(self, cfg, db, private_link):
        """Текстовое сообщение → text.txt + meta.json + manifest.json."""
        from app.pipeline.collector import CollectorOrchestrator

        job_id = db.create_job(private_link)
        orch = CollectorOrchestrator(cfg, db)

        mock_message = _make_text_message()

        with patch.object(orch, '_fetch_message', new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_message
            result = orch.run(job_id, private_link, MagicMock(), from_start=False)

        assert "collected_dir" in result
        collected_dir = Path(result["collected_dir"])

        # Проверяем файлы
        assert (collected_dir / "text.txt").exists()
        assert (collected_dir / "meta.json").exists()
        assert (collected_dir / "manifest.json").exists()
        assert not (collected_dir / "transcript.txt").exists()
        assert not (collected_dir / "attachments").exists()

        # Проверяем text.txt — verbatim
        text = (collected_dir / "text.txt").read_text(encoding="utf-8")
        assert text == "Привет, это тестовое сообщение!"

        # Проверяем meta.json
        meta = json.loads((collected_dir / "meta.json").read_text(encoding="utf-8"))
        assert meta["msg_id"] == private_link.msg_id
        assert meta["message_type"] == "text_only"
        assert meta["has_text"] is True
        assert meta["has_transcript"] is False
        assert meta["files"] == []

        # Проверяем manifest.json
        manifest = json.loads((collected_dir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["version"] == 1
        assert manifest["message_type"] == "text_only"
        assert len(manifest["artifacts"]) == 1
        assert manifest["artifacts"][0]["type"] == "text"

        # Проверяем export в БД
        exports = db.get_exports(job_id)
        assert len(exports) == 1
        assert exports[0]["export_type"] == "collected"

        # Проверяем статус
        job = db.get_job_by_id(job_id)
        assert job["status"] == "done"


# ─── Text + Images Tests ─────────────────────────────────────

class TestCollectTextWithImages:

    def test_collect_text_with_images(self, cfg, db, private_link):
        """Сообщение с фото → text.txt + attachments/image.jpg + meta + manifest."""
        from app.pipeline.collector import CollectorOrchestrator

        job_id = db.create_job(private_link)
        orch = CollectorOrchestrator(cfg, db)
        collected_dir = orch._collected_dir(private_link)

        mock_message = MagicMock()
        mock_message.text = "Фото с подписью"
        mock_message.media = _make_photo_media()
        mock_message.date = datetime(2025, 1, 15, 10, 30, 0)
        mock_message.forward = None
        mock_message.grouped_id = None
        mock_message.id = 1197

        # Мокаем download_media
        async def mock_download(msg, file):
            attachments_dir = collected_dir / "attachments"
            attachments_dir.mkdir(parents=True, exist_ok=True)
            fake_path = attachments_dir / "photo.jpg"
            fake_path.write_bytes(b"fake_image_data_1234567890")
            return str(fake_path)

        mock_client = MagicMock()
        mock_client.download_media = AsyncMock(side_effect=mock_download)

        with patch.object(orch, '_fetch_message', new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_message
            result = orch.run(job_id, private_link, mock_client, from_start=False)

        collected_dir = Path(result["collected_dir"])
        assert (collected_dir / "text.txt").exists()
        assert (collected_dir / "attachments").exists()
        assert (collected_dir / "meta.json").exists()
        assert (collected_dir / "manifest.json").exists()

        # Проверяем manifest
        manifest = json.loads((collected_dir / "manifest.json").read_text(encoding="utf-8"))
        types = [a["type"] for a in manifest["artifacts"]]
        assert "text" in types
        assert "attachment" in types


# ─── Text + Docs Tests ───────────────────────────────────────

class TestCollectTextWithDocs:

    def test_collect_text_with_docs(self, cfg, db, private_link):
        """Сообщение с документом → text.txt + attachments/doc.pdf + meta + manifest."""
        from app.pipeline.collector import CollectorOrchestrator

        job_id = db.create_job(private_link)
        orch = CollectorOrchestrator(cfg, db)
        collected_dir = orch._collected_dir(private_link)

        mock_message = MagicMock()
        mock_message.text = "Документ с подписью"
        mock_message.media = _make_doc_media("application/pdf", size=5000)
        mock_message.date = datetime(2025, 1, 15, 10, 30, 0)
        mock_message.forward = None
        mock_message.grouped_id = None
        mock_message.id = 1197

        async def mock_download(msg, file):
            attachments_dir = collected_dir / "attachments"
            attachments_dir.mkdir(parents=True, exist_ok=True)
            fake_path = attachments_dir / "report.pdf"
            fake_path.write_bytes(b"fake_pdf_data_12345")
            return str(fake_path)

        mock_client = MagicMock()
        mock_client.download_media = AsyncMock(side_effect=mock_download)

        with patch.object(orch, '_fetch_message', new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_message
            result = orch.run(job_id, private_link, mock_client, from_start=False)

        collected_dir = Path(result["collected_dir"])
        assert (collected_dir / "text.txt").exists()
        assert (collected_dir / "attachments").exists()
        assert (collected_dir / "manifest.json").exists()

        meta = json.loads((collected_dir / "meta.json").read_text(encoding="utf-8"))
        assert meta["message_type"] == "text_with_docs"


# ─── Audio/Video Tests ───────────────────────────────────────

class TestCollectAudioVideo:

    def test_collect_audio_video(self, cfg, db, private_link):
        """Видео → attachments/video.mp4 + transcript.txt + meta + manifest (mock Transcriber)."""
        from app.pipeline.collector import CollectorOrchestrator
        from app.pipeline.transcriber import TranscriptResult, Segment

        job_id = db.create_job(private_link)
        orch = CollectorOrchestrator(cfg, db)
        collected_dir = orch._collected_dir(private_link)

        mock_message = MagicMock()
        mock_message.text = "Видео с подписью"
        mock_message.media = _make_video_media()
        mock_message.date = datetime(2025, 1, 15, 10, 30, 0)
        mock_message.forward = None
        mock_message.grouped_id = None
        mock_message.id = 1197

        async def mock_download(msg, file):
            attachments_dir = collected_dir / "attachments"
            attachments_dir.mkdir(parents=True, exist_ok=True)
            fake_path = attachments_dir / "video.mp4"
            fake_path.write_bytes(b"fake_video_data" * 100)
            return str(fake_path)

        mock_client = MagicMock()
        mock_client.download_media = AsyncMock(side_effect=mock_download)

        # Мокаем Transcriber
        mock_transcript = TranscriptResult(
            segments=[
                Segment(start=0.0, end=5.0, text="Привет мир", avg_logprob=-0.3),
                Segment(start=5.0, end=10.0, text="Это тест", avg_logprob=-0.4),
            ],
            language="ru",
            model_used="large-v3",
            duration_sec=10.0,
            word_count=4,
            unrecognized_count=0,
        )

        with patch.object(orch, '_fetch_message', new_callable=AsyncMock) as mock_fetch, \
             patch('app.pipeline.transcriber.Transcriber.transcribe', return_value=mock_transcript):
            mock_fetch.return_value = mock_message

            result = orch.run(job_id, private_link, mock_client, from_start=False)

        collected_dir = Path(result["collected_dir"])
        assert (collected_dir / "text.txt").exists()
        assert (collected_dir / "attachments" / "video.mp4").exists()
        assert (collected_dir / "transcript.txt").exists()
        assert (collected_dir / "meta.json").exists()
        assert (collected_dir / "manifest.json").exists()

        # Проверяем meta
        meta = json.loads((collected_dir / "meta.json").read_text(encoding="utf-8"))
        assert meta["message_type"] == "audio_video"
        assert meta["has_transcript"] is True
        assert meta["transcript_language"] == "ru"
        assert meta["transcript_word_count"] == 4

        # Проверяем manifest
        manifest = json.loads((collected_dir / "manifest.json").read_text(encoding="utf-8"))
        types = [a["type"] for a in manifest["artifacts"]]
        assert "text" in types
        assert "transcript" in types
        assert "attachment" in types


# ─── Album Tests ──────────────────────────────────────────────

class TestCollectAlbum:

    def test_collect_album(self, cfg, db, private_link):
        """Альбом из нескольких сообщений → все медиа + combined text."""
        from app.pipeline.collector import CollectorOrchestrator

        job_id = db.create_job(private_link)
        orch = CollectorOrchestrator(cfg, db)
        collected_dir = orch._collected_dir(private_link)

        # Создаём 3 сообщения альбома
        msg1 = MagicMock()
        msg1.text = "Первое фото"
        msg1.media = _make_photo_media()
        msg1.date = datetime(2025, 1, 15, 10, 30, 0)
        msg1.forward = None
        msg1.grouped_id = 12345
        msg1.id = 1195

        msg2 = MagicMock()
        msg2.text = "Второе фото"
        msg2.media = _make_photo_media()
        msg2.date = datetime(2025, 1, 15, 10, 30, 0)
        msg2.forward = None
        msg2.grouped_id = 12345
        msg2.id = 1196

        msg3 = MagicMock()
        msg3.text = None
        msg3.media = _make_photo_media()
        msg3.date = datetime(2025, 1, 15, 10, 30, 0)
        msg3.forward = None
        msg3.grouped_id = 12345
        msg3.id = 1197

        download_counter = [0]

        async def mock_download(msg, file):
            download_counter[0] += 1
            attachments_dir = collected_dir / "attachments"
            attachments_dir.mkdir(parents=True, exist_ok=True)
            fake_path = attachments_dir / f"photo_{download_counter[0]}.jpg"
            fake_path.write_bytes(b"fake_image_data")
            return str(fake_path)

        mock_client = MagicMock()
        mock_client.download_media = AsyncMock(side_effect=mock_download)

        with patch.object(orch, '_fetch_message', new_callable=AsyncMock) as mock_fetch, \
             patch.object(orch, '_fetch_album', new_callable=AsyncMock) as mock_album:
            mock_fetch.return_value = msg3  # primary message
            mock_album.return_value = [msg1, msg2, msg3]  # album
            result = orch.run(job_id, private_link, mock_client, from_start=False)

        collected_dir = Path(result["collected_dir"])

        # Проверяем combined text
        text = (collected_dir / "text.txt").read_text(encoding="utf-8")
        assert "Первое фото" in text
        assert "Второе фото" in text
        assert "---" in text  # separator

        # Проверяем meta (album info)
        meta = json.loads((collected_dir / "meta.json").read_text(encoding="utf-8"))
        assert "album" in meta
        assert meta["album"]["grouped_id"] == 12345
        assert 1195 in meta["album"]["message_ids"]
        assert 1196 in meta["album"]["message_ids"]
        assert 1197 in meta["album"]["message_ids"]

        # 3 attachment artifacts
        manifest = json.loads((collected_dir / "manifest.json").read_text(encoding="utf-8"))
        attachments = [a for a in manifest["artifacts"] if a["type"] == "attachment"]
        assert len(attachments) == 3


# ─── Idempotency Tests ───────────────────────────────────────

class TestCollectIdempotency:

    def test_second_run_returns_cached(self, cfg, db, private_link):
        """Повторный запуск без from_start → кэшированный результат, 1 export."""
        from app.pipeline.collector import CollectorOrchestrator

        job_id = db.create_job(private_link)
        orch = CollectorOrchestrator(cfg, db)

        mock_message = _make_text_message()

        with patch.object(orch, '_fetch_message', new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_message
            result1 = orch.run(job_id, private_link, MagicMock(), from_start=False)
            # Second run — should hit cache (manifest.json exists)
            result2 = orch.run(job_id, private_link, MagicMock(), from_start=False)

        assert result1["collected_dir"] == result2["collected_dir"]

        # Только одна export запись
        exports = db.get_exports(job_id)
        assert len(exports) == 1


# ─── From Start Tests ────────────────────────────────────────

class TestCollectFromStart:

    def test_from_start_overwrites(self, cfg, db, private_link):
        """--from-start перезаписывает существующие данные."""
        from app.pipeline.collector import CollectorOrchestrator

        job_id = db.create_job(private_link)
        orch = CollectorOrchestrator(cfg, db)

        msg1 = _make_text_message("Оригинальный текст")

        with patch.object(orch, '_fetch_message', new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = msg1
            result1 = orch.run(job_id, private_link, MagicMock(), from_start=False)

        msg2 = _make_text_message("Обновлённый текст")

        with patch.object(orch, '_fetch_message', new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = msg2
            result2 = orch.run(job_id, private_link, MagicMock(), from_start=True)

        collected_dir = Path(result2["collected_dir"])
        text = (collected_dir / "text.txt").read_text(encoding="utf-8")
        assert text == "Обновлённый текст"


# ─── Empty Message Tests ─────────────────────────────────────

class TestCollectEmpty:

    def test_none_message_raises(self, cfg, db, private_link):
        """None message → MediaNotFoundError."""
        from app.pipeline.collector import CollectorOrchestrator
        from app.pipeline.downloader import MediaNotFoundError

        job_id = db.create_job(private_link)
        orch = CollectorOrchestrator(cfg, db)

        with patch.object(orch, '_fetch_message', new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = None
            with pytest.raises(MediaNotFoundError):
                orch.run(job_id, private_link, MagicMock(), from_start=False)

    def test_empty_message_raises(self, cfg, db, private_link):
        """Сообщение без текста и медиа → MediaNotFoundError."""
        from app.pipeline.collector import CollectorOrchestrator
        from app.pipeline.downloader import MediaNotFoundError

        job_id = db.create_job(private_link)
        orch = CollectorOrchestrator(cfg, db)

        mock_message = MagicMock()
        mock_message.text = None
        mock_message.media = None
        mock_message.date = datetime(2025, 1, 15, 10, 30, 0)
        mock_message.forward = None
        mock_message.grouped_id = None
        mock_message.id = 1197

        with patch.object(orch, '_fetch_message', new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_message
            with pytest.raises(MediaNotFoundError, match="Пустое сообщение"):
                orch.run(job_id, private_link, MagicMock(), from_start=False)


# ─── Worker Routing Tests ────────────────────────────────────

class TestWorkerRoutesCollect:

    def test_worker_routes_collect_jobs(self, cfg, db, private_link):
        """Worker отправляет 'collect' задачи к CollectorOrchestrator."""
        from app.queue.worker import Worker

        job_id = db.create_job(private_link)  # default is now "collect"
        worker = Worker(cfg, db)

        mock_result = {"collected_dir": "/tmp/test"}

        with patch.object(worker.collector, 'run', return_value=mock_result) as mock_run:
            mock_client = MagicMock()
            result = worker.process(job_id, private_link, mock_client)

        mock_run.assert_called_once()
        assert result == mock_result


# ─── DB Default Tests ────────────────────────────────────────

class TestDbDefaultJobType:

    def test_create_job_defaults_to_collect(self, db, private_link):
        """create_job() без аргументов → job_type='collect'."""
        job_id = db.create_job(private_link)
        job = db.get_job_by_id(job_id)
        assert job["job_type"] == "collect"

    def test_create_job_explicit_media(self, db):
        """Явное указание 'media' всё ещё работает."""
        link = TelegramLink(chat_id=999, msg_id=1, raw_url="https://t.me/c/999/1")
        job_id = db.create_job(link, job_type="media")
        job = db.get_job_by_id(job_id)
        assert job["job_type"] == "media"

    def test_create_job_explicit_ingest(self, db):
        """Явное указание 'ingest' всё ещё работает."""
        link = TelegramLink(chat_id=999, msg_id=2, raw_url="https://t.me/c/999/2")
        job_id = db.create_job(link, job_type="ingest")
        job = db.get_job_by_id(job_id)
        assert job["job_type"] == "ingest"
