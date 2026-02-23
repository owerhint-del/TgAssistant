"""
Тесты ingest-пайплайна: классификация, сохранение текста/изображений/документов, идемпотентность.
"""
import json
import os
import sys
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.url_parser import TelegramLink, parse_url
from app.pipeline.classifier import MessageType, classify, _is_audio_video_media, _is_image, _is_document


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
        anthropic_api_key="",
        pdf_font_path="./fonts/DejaVuSans.ttf",
        pdf_bold_font_path="",
        cleanup_temp=True,
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


# ─── URL Parser Tests ───────────────────────────────────────

class TestPublicChannelParsing:

    def test_public_channel_basic(self):
        link = parse_url("https://t.me/durov/42")
        assert link.chat_id == 0
        assert link.msg_id == 42
        assert link.channel_username == "durov"

    def test_public_channel_with_underscore(self):
        link = parse_url("https://t.me/my_channel_123/99")
        assert link.channel_username == "my_channel_123"
        assert link.msg_id == 99

    def test_public_channel_http(self):
        link = parse_url("http://t.me/testchannel/1")
        assert link.channel_username == "testchannel"

    def test_private_still_works(self):
        link = parse_url("https://t.me/c/9999/42")
        assert link.chat_id == 9999
        assert link.msg_id == 42
        assert link.channel_username is None

    def test_reserved_path_rejected(self):
        with pytest.raises(ValueError, match="зарезервированный путь"):
            parse_url("https://t.me/joinchat/42")

    def test_short_username_rejected(self):
        """Username должен быть минимум 5 символов."""
        with pytest.raises(ValueError):
            parse_url("https://t.me/abc/42")


# ─── Classifier Tests ───────────────────────────────────────

def _make_video_media():
    """Мок медиа с видео."""
    from telethon.tl.types import DocumentAttributeVideo
    attr = DocumentAttributeVideo(duration=120, w=1920, h=1080)
    doc = MagicMock()
    doc.mime_type = "video/mp4"
    doc.attributes = [attr]
    media = MagicMock(spec=['document'])
    media.document = doc
    media.__class__ = type('MessageMediaDocument', (), {})
    return media


def _make_audio_media():
    """Мок медиа с аудио."""
    from telethon.tl.types import DocumentAttributeAudio
    attr = DocumentAttributeAudio(duration=60, voice=False)
    doc = MagicMock()
    doc.mime_type = "audio/mpeg"
    doc.attributes = [attr]
    media = MagicMock(spec=['document'])
    media.document = doc
    media.__class__ = type('MessageMediaDocument', (), {})
    return media


def _make_photo_media():
    """Мок медиа с фото."""
    from telethon.tl.types import MessageMediaPhoto
    media = MagicMock(spec=MessageMediaPhoto)
    media.__class__ = MessageMediaPhoto
    return media


def _make_doc_media(mime_type="application/pdf"):
    """Мок медиа с документом (не аудио/видео)."""
    from telethon.tl.types import MessageMediaDocument
    doc = MagicMock()
    doc.mime_type = mime_type
    doc.attributes = []  # нет Video/Audio атрибутов
    media = MagicMock(spec=MessageMediaDocument)
    media.__class__ = MessageMediaDocument
    media.document = doc
    return media


class TestClassifierHelpers:

    def test_is_audio_video_with_video(self):
        media = _make_doc_media("video/mp4")
        from telethon.tl.types import DocumentAttributeVideo, MessageMediaDocument
        attr = DocumentAttributeVideo(duration=120, w=1920, h=1080)
        media.document.attributes = [attr]
        media.__class__ = MessageMediaDocument
        assert _is_audio_video_media(media) is True

    def test_is_audio_video_with_pdf(self):
        media = _make_doc_media("application/pdf")
        assert _is_audio_video_media(media) is False

    def test_is_image_with_photo(self):
        media = _make_photo_media()
        assert _is_image(media) is True

    def test_is_image_with_pdf(self):
        media = _make_doc_media("application/pdf")
        assert _is_image(media) is False

    def test_is_document_with_pdf(self):
        media = _make_doc_media("application/pdf")
        assert _is_document(media) is True

    def test_is_document_excludes_video(self):
        from telethon.tl.types import DocumentAttributeVideo, MessageMediaDocument
        media = _make_doc_media("video/mp4")
        media.__class__ = MessageMediaDocument
        attr = DocumentAttributeVideo(duration=120, w=1920, h=1080)
        media.document.attributes = [attr]
        assert _is_document(media) is False


# ─── Database Tests ──────────────────────────────────────────

class TestDatabaseJobType:

    def test_create_job_default_type(self, db, private_link):
        job_id = db.create_job(private_link)
        job = db.get_job_by_id(job_id)
        assert job["job_type"] == "media"

    def test_create_job_ingest_type(self, db, private_link):
        job_id = db.create_job(private_link, job_type="ingest")
        job = db.get_job_by_id(job_id)
        assert job["job_type"] == "ingest"

    def test_update_job_type(self, db, private_link):
        job_id = db.create_job(private_link)
        db.update_job_status(job_id, "pending", job_type="ingest")
        job = db.get_job_by_id(job_id)
        assert job["job_type"] == "ingest"

    def test_public_link_in_db(self, db, public_link):
        job_id = db.create_job(public_link)
        job = db.get_job_by_id(job_id)
        assert job["chat_id"] == 0
        assert job["url"] == public_link.raw_url


# ─── IngestOrchestrator Tests ────────────────────────────────

class TestIngestOrchestrator:

    def test_wiki_dir_private_channel(self, cfg, db, private_link):
        from app.pipeline.ingest_orchestrator import IngestOrchestrator
        orch = IngestOrchestrator(cfg, db)
        wiki_dir = orch._wiki_dir(private_link)
        assert "wiki" in str(wiki_dir)
        assert str(private_link.chat_id) in str(wiki_dir)
        assert str(private_link.msg_id) in str(wiki_dir)

    def test_wiki_dir_public_channel(self, cfg, db, public_link):
        from app.pipeline.ingest_orchestrator import IngestOrchestrator
        orch = IngestOrchestrator(cfg, db)
        wiki_dir = orch._wiki_dir(public_link)
        assert "wiki" in str(wiki_dir)
        assert "durov" in str(wiki_dir)
        assert str(public_link.msg_id) in str(wiki_dir)

    def test_ingest_text_only(self, cfg, db, private_link):
        """Тест: текстовое сообщение сохраняется 1:1."""
        from app.pipeline.ingest_orchestrator import IngestOrchestrator
        from datetime import datetime

        job_id = db.create_job(private_link, job_type="ingest")
        orch = IngestOrchestrator(cfg, db)

        # Мокаем сообщение с текстом, без медиа
        mock_message = MagicMock()
        mock_message.text = "Привет, это тестовое сообщение!"
        mock_message.media = None
        mock_message.date = datetime(2025, 1, 15, 10, 30, 0)
        mock_message.forward = None

        with patch.object(orch, '_fetch_message', new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_message
            result = orch.run(job_id, private_link, MagicMock(), from_start=False)

        assert "wiki_dir" in result
        wiki_dir = Path(result["wiki_dir"])

        # Проверяем структуру файлов
        assert (wiki_dir / "text.txt").exists()
        assert (wiki_dir / "meta.json").exists()

        # Проверяем содержимое text.txt (1:1, verbatim)
        text = (wiki_dir / "text.txt").read_text(encoding="utf-8")
        assert text == "Привет, это тестовое сообщение!"

        # Проверяем meta.json
        meta = json.loads((wiki_dir / "meta.json").read_text(encoding="utf-8"))
        assert meta["msg_id"] == private_link.msg_id
        assert meta["has_text"] is True
        assert meta["text_length"] == len(mock_message.text)
        assert meta["files"] == []

        # Проверяем что export сохранён в БД
        exports = db.get_exports(job_id)
        assert len(exports) == 1
        assert exports[0]["export_type"] == "ingest_wiki"

        # Проверяем статус задачи
        job = db.get_job_by_id(job_id)
        assert job["status"] == "done"

    def test_ingest_with_photo(self, cfg, db, private_link):
        """Тест: сообщение с фото — фото скачивается в images/."""
        from app.pipeline.ingest_orchestrator import IngestOrchestrator
        from datetime import datetime

        job_id = db.create_job(private_link, job_type="ingest")
        orch = IngestOrchestrator(cfg, db)

        mock_message = MagicMock()
        mock_message.text = "Фото с подписью"
        mock_message.media = _make_photo_media()
        mock_message.date = datetime(2025, 1, 15, 10, 30, 0)
        mock_message.forward = None

        # Мокаем скачивание: download_media возвращает путь к файлу
        wiki_dir = orch._wiki_dir(private_link)
        wiki_dir.mkdir(parents=True, exist_ok=True)

        async def mock_download(msg, file):
            # Создаём фейковый файл
            images_dir = wiki_dir / "images"
            images_dir.mkdir(exist_ok=True)
            fake_path = images_dir / "photo.jpg"
            fake_path.write_bytes(b"fake_image_data")
            return str(fake_path)

        mock_client = MagicMock()
        mock_client.download_media = AsyncMock(side_effect=mock_download)

        with patch.object(orch, '_fetch_message', new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_message
            result = orch.run(job_id, private_link, mock_client, from_start=False)

        wiki_dir = Path(result["wiki_dir"])
        assert (wiki_dir / "text.txt").exists()
        assert (wiki_dir / "images").exists()
        assert (wiki_dir / "meta.json").exists()

    def test_ingest_idempotency(self, cfg, db, private_link):
        """Повторный ingest тех же данных не дублирует export записи."""
        from app.pipeline.ingest_orchestrator import IngestOrchestrator
        from datetime import datetime

        job_id = db.create_job(private_link, job_type="ingest")
        orch = IngestOrchestrator(cfg, db)

        mock_message = MagicMock()
        mock_message.text = "Тест идемпотентности"
        mock_message.media = None
        mock_message.date = datetime(2025, 1, 15, 10, 30, 0)
        mock_message.forward = None

        with patch.object(orch, '_fetch_message', new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_message
            # Первый запуск
            result1 = orch.run(job_id, private_link, MagicMock(), from_start=False)
            # Второй запуск (resume) — должен использовать кэш
            result2 = orch.run(job_id, private_link, MagicMock(), from_start=False)

        assert result1["wiki_dir"] == result2["wiki_dir"]

        # Только одна export запись
        exports = db.get_exports(job_id)
        assert len(exports) == 1

    def test_ingest_from_start_overwrites(self, cfg, db, private_link):
        """--from-start перезаписывает существующие данные."""
        from app.pipeline.ingest_orchestrator import IngestOrchestrator
        from datetime import datetime

        job_id = db.create_job(private_link, job_type="ingest")
        orch = IngestOrchestrator(cfg, db)

        mock_message = MagicMock()
        mock_message.text = "Оригинальный текст"
        mock_message.media = None
        mock_message.date = datetime(2025, 1, 15, 10, 30, 0)
        mock_message.forward = None

        with patch.object(orch, '_fetch_message', new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_message
            result1 = orch.run(job_id, private_link, MagicMock(), from_start=False)

        # Меняем текст, запускаем с from_start
        mock_message.text = "Обновлённый текст"

        with patch.object(orch, '_fetch_message', new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_message
            result2 = orch.run(job_id, private_link, MagicMock(), from_start=True)

        wiki_dir = Path(result2["wiki_dir"])
        text = (wiki_dir / "text.txt").read_text(encoding="utf-8")
        assert text == "Обновлённый текст"

    def test_ingest_meta_with_forward(self, cfg, db, private_link):
        """Пересланное сообщение: forward info записывается в meta.json."""
        from app.pipeline.ingest_orchestrator import IngestOrchestrator
        from datetime import datetime

        job_id = db.create_job(private_link, job_type="ingest")
        orch = IngestOrchestrator(cfg, db)

        mock_forward = MagicMock()
        mock_forward.from_name = "Test User"
        mock_forward.date = datetime(2025, 1, 14, 8, 0, 0)
        mock_forward.channel_post = 99
        mock_forward.from_id = None

        mock_message = MagicMock()
        mock_message.text = "Пересланное сообщение"
        mock_message.media = None
        mock_message.date = datetime(2025, 1, 15, 10, 30, 0)
        mock_message.forward = mock_forward

        with patch.object(orch, '_fetch_message', new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_message
            result = orch.run(job_id, private_link, MagicMock(), from_start=False)

        wiki_dir = Path(result["wiki_dir"])
        meta = json.loads((wiki_dir / "meta.json").read_text(encoding="utf-8"))
        assert "forward" in meta
        assert meta["forward"]["from_name"] == "Test User"
        assert meta["forward"]["channel_post"] == 99

    def test_ingest_empty_message_raises(self, cfg, db, private_link):
        """Пустое сообщение (без текста и медиа) — ошибка."""
        from app.pipeline.ingest_orchestrator import IngestOrchestrator
        from app.pipeline.downloader import MediaNotFoundError

        job_id = db.create_job(private_link, job_type="ingest")
        orch = IngestOrchestrator(cfg, db)

        # None message — not found
        with patch.object(orch, '_fetch_message', new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = None
            with pytest.raises(MediaNotFoundError):
                orch.run(job_id, private_link, MagicMock(), from_start=False)


class TestIngestFileStructure:
    """Проверяем, что структура wiki-папки соответствует спецификации."""

    def test_text_only_structure(self, cfg, db, private_link):
        from app.pipeline.ingest_orchestrator import IngestOrchestrator
        from datetime import datetime

        job_id = db.create_job(private_link, job_type="ingest")
        orch = IngestOrchestrator(cfg, db)

        mock_message = MagicMock()
        mock_message.text = "Текст"
        mock_message.media = None
        mock_message.date = datetime(2025, 1, 15, 10, 30, 0)
        mock_message.forward = None

        with patch.object(orch, '_fetch_message', new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_message
            result = orch.run(job_id, private_link, MagicMock(), from_start=False)

        wiki_dir = Path(result["wiki_dir"])

        # Должны быть только text.txt и meta.json (без images/ или docs/)
        contents = list(wiki_dir.iterdir())
        names = {f.name for f in contents}
        assert names == {"text.txt", "meta.json"}

    def test_wiki_path_format(self, cfg, db, private_link):
        """Путь: <output_dir>/wiki/<chat_id>/<msg_id>/."""
        from app.pipeline.ingest_orchestrator import IngestOrchestrator

        orch = IngestOrchestrator(cfg, db)
        wiki_dir = orch._wiki_dir(private_link)

        parts = wiki_dir.parts
        assert "wiki" in parts
        wiki_idx = parts.index("wiki")
        assert parts[wiki_idx + 1] == str(private_link.chat_id)
        assert parts[wiki_idx + 2] == str(private_link.msg_id)
