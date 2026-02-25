"""
Tests for media cleanup in collected/ directories.
"""
import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.utils.cleanup import cleanup_media, CleanupResult


class TestCleanupMedia(unittest.TestCase):
    """Тесты для cleanup_media()."""

    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.output_dir = self.tmpdir.name
        self.collected = Path(self.output_dir) / "collected" / "channel" / "123"
        self.collected.mkdir(parents=True)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _create_file(self, name, size=1024, age_days=10):
        """Создаёт файл с заданным размером и возрастом."""
        path = self.collected / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00" * size)
        old_time = time.time() - (age_days * 86400)
        os.utime(path, (old_time, old_time))
        return path

    def test_deletes_old_video(self):
        """Старое видео удаляется."""
        self._create_file("attachments/video.mp4", size=1000, age_days=10)
        result = cleanup_media(self.output_dir, older_than_days=7)
        self.assertEqual(result.files_deleted, 1)
        self.assertEqual(result.bytes_freed, 1000)

    def test_deletes_old_audio(self):
        """Старое аудио удаляется."""
        self._create_file("attachments/audio.wav", age_days=10)
        self._create_file("attachments/voice.ogg", age_days=10)
        result = cleanup_media(self.output_dir, older_than_days=7)
        self.assertEqual(result.files_deleted, 2)

    def test_keeps_recent_video(self):
        """Свежее видео НЕ удаляется."""
        self._create_file("attachments/new_video.mp4", age_days=3)
        result = cleanup_media(self.output_dir, older_than_days=7)
        self.assertEqual(result.files_deleted, 0)
        self.assertEqual(result.files_skipped, 1)

    def test_keeps_text_files(self):
        """text.txt, transcript.txt, meta.json сохраняются."""
        self._create_file("text.txt", age_days=30)
        self._create_file("transcript.txt", age_days=30)
        self._create_file("meta.json", age_days=30)
        self._create_file("manifest.json", age_days=30)
        result = cleanup_media(self.output_dir, older_than_days=7)
        self.assertEqual(result.files_deleted, 0)

    def test_keeps_images(self):
        """Изображения сохраняются."""
        self._create_file("attachments/photo.jpg", age_days=30)
        self._create_file("attachments/image.png", age_days=30)
        result = cleanup_media(self.output_dir, older_than_days=7)
        self.assertEqual(result.files_deleted, 0)

    def test_dry_run_no_delete(self):
        """dry_run считает, но не удаляет."""
        path = self._create_file("attachments/video.mp4", size=5000, age_days=10)
        result = cleanup_media(self.output_dir, older_than_days=7, dry_run=True)
        self.assertEqual(result.files_deleted, 1)
        self.assertEqual(result.bytes_freed, 5000)
        self.assertTrue(path.exists(), "файл не должен быть удалён в dry_run")

    def test_mixed_files(self):
        """Микс файлов: удаляет только старые AV."""
        self._create_file("attachments/old_video.mp4", size=2000, age_days=10)
        self._create_file("attachments/new_video.mp4", size=3000, age_days=2)
        self._create_file("attachments/photo.jpg", size=100, age_days=30)
        self._create_file("text.txt", size=50, age_days=30)
        self._create_file("meta.json", size=30, age_days=30)

        result = cleanup_media(self.output_dir, older_than_days=7)
        self.assertEqual(result.files_deleted, 1)
        self.assertEqual(result.bytes_freed, 2000)
        self.assertEqual(result.files_skipped, 1)  # new_video.mp4

    def test_empty_output_dir(self):
        """Нет collected/ папки → ничего не делает."""
        empty = TemporaryDirectory()
        result = cleanup_media(empty.name, older_than_days=7)
        self.assertEqual(result.files_deleted, 0)
        empty.cleanup()

    def test_multiple_extensions(self):
        """Проверяет все AV-расширения."""
        for ext in [".mp4", ".mov", ".avi", ".mkv", ".webm", ".wav", ".mp3", ".ogg", ".aac", ".flac"]:
            self._create_file(f"attachments/file{ext}", age_days=10)
        result = cleanup_media(self.output_dir, older_than_days=7)
        self.assertEqual(result.files_deleted, 10)

    def test_nested_collected_dirs(self):
        """Рекурсивно обходит вложенные папки."""
        # channel/123/attachments/
        self._create_file("attachments/video1.mp4", age_days=10)

        # Второй канал
        other = Path(self.output_dir) / "collected" / "other_channel" / "456" / "attachments"
        other.mkdir(parents=True)
        f = other / "video2.mp4"
        f.write_bytes(b"\x00" * 500)
        old_time = time.time() - (10 * 86400)
        os.utime(f, (old_time, old_time))

        result = cleanup_media(self.output_dir, older_than_days=7)
        self.assertEqual(result.files_deleted, 2)

    def test_external_collected(self):
        """Чистит external/ так же как обычный collected/."""
        ext_dir = Path(self.output_dir) / "collected" / "external" / "youtube" / "abc123" / "attachments"
        ext_dir.mkdir(parents=True)
        f = ext_dir / "video.mp4"
        f.write_bytes(b"\x00" * 3000)
        old_time = time.time() - (15 * 86400)
        os.utime(f, (old_time, old_time))

        result = cleanup_media(self.output_dir, older_than_days=7)
        self.assertEqual(result.files_deleted, 1)
        self.assertEqual(result.bytes_freed, 3000)


class TestCleanupResult(unittest.TestCase):
    """Тесты для dataclass CleanupResult."""

    def test_default_values(self):
        r = CleanupResult()
        self.assertEqual(r.files_deleted, 0)
        self.assertEqual(r.bytes_freed, 0)
        self.assertEqual(r.files_skipped, 0)
        self.assertEqual(r.errors, 0)


if __name__ == "__main__":
    unittest.main()
