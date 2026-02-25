"""
Tests for relative path storage in exports and backward compatibility.
"""
import unittest
from pathlib import Path

from app.config import Config
from app.db.database import Database


class TestToRelative(unittest.TestCase):
    """Тесты для _to_relative(): преобразование абсолютного пути в относительный."""

    def setUp(self):
        self.db = Database(":memory:", output_dir="/home/user/output")
        self.db.connect()
        self.db.migrate()

    def tearDown(self):
        self.db.close()

    def test_strips_output_dir_prefix(self):
        result = self.db._to_relative("/home/user/output/collected/123/456")
        self.assertEqual(result, "collected/123/456")

    def test_strips_with_trailing_slash(self):
        db = Database(":memory:", output_dir="/home/user/output/")
        result = db._to_relative("/home/user/output/collected/foo")
        self.assertEqual(result, "collected/foo")

    def test_non_matching_prefix_kept_absolute(self):
        result = self.db._to_relative("/other/path/collected/123")
        self.assertEqual(result, "/other/path/collected/123")

    def test_no_output_dir_kept_absolute(self):
        db = Database(":memory:", output_dir=None)
        result = db._to_relative("/home/user/output/collected/123")
        self.assertEqual(result, "/home/user/output/collected/123")


class TestResolvePath(unittest.TestCase):
    """Тесты для _resolve_path(): резолвинг пути из БД."""

    def setUp(self):
        self.db = Database(":memory:", output_dir="/home/user/output")
        self.db.connect()
        self.db.migrate()

    def tearDown(self):
        self.db.close()

    def test_relative_path_resolved(self):
        result = self.db._resolve_path("collected/123/456")
        self.assertEqual(result, "/home/user/output/collected/123/456")

    def test_absolute_path_unchanged(self):
        result = self.db._resolve_path("/old/absolute/path/collected/123")
        self.assertEqual(result, "/old/absolute/path/collected/123")

    def test_empty_path_unchanged(self):
        result = self.db._resolve_path("")
        self.assertEqual(result, "")

    def test_no_output_dir_relative_unchanged(self):
        db = Database(":memory:", output_dir=None)
        result = db._resolve_path("collected/123/456")
        self.assertEqual(result, "collected/123/456")


class TestSaveExportRelative(unittest.TestCase):
    """Тесты для save_export(): сохраняет относительный путь в БД."""

    def setUp(self):
        self.db = Database(":memory:", output_dir="/home/user/output")
        self.db.connect()
        self.db.migrate()

    def tearDown(self):
        self.db.close()

    def _create_job(self):
        """Создаёт тестовую задачу и возвращает job_id."""
        from app.utils.url_parser import TelegramLink
        link = TelegramLink(chat_id=123, msg_id=1, raw_url="https://t.me/c/123/1")
        return self.db.create_job(link)

    def test_new_export_stored_relative(self):
        job_id = self._create_job()
        self.db.save_export(job_id, "collected", "/home/user/output/collected/123/1")

        # Читаем сырое значение из БД напрямую
        row = self.db.conn.execute(
            "SELECT file_path FROM exports WHERE job_id = ?", (job_id,)
        ).fetchone()
        self.assertEqual(row[0], "collected/123/1")

    def test_get_exports_resolves_back(self):
        job_id = self._create_job()
        self.db.save_export(job_id, "collected", "/home/user/output/collected/123/1")

        exports = self.db.get_exports(job_id)
        self.assertEqual(len(exports), 1)
        self.assertEqual(exports[0]["file_path"], "/home/user/output/collected/123/1")


class TestBackwardCompatibility(unittest.TestCase):
    """Тесты обратной совместимости: старые абсолютные пути читаются корректно."""

    def setUp(self):
        self.db = Database(":memory:", output_dir="/new/output")
        self.db.connect()
        self.db.migrate()

    def tearDown(self):
        self.db.close()

    def _create_job(self):
        from app.utils.url_parser import TelegramLink
        link = TelegramLink(chat_id=123, msg_id=1, raw_url="https://t.me/c/123/1")
        return self.db.create_job(link)

    def test_old_absolute_path_read_as_is(self):
        """Старая запись с абсолютным путём читается без изменений."""
        job_id = self._create_job()

        # Вставляем вручную как старая система (абсолютный путь)
        self.db.conn.execute(
            "INSERT INTO exports (id, job_id, export_type, file_path) VALUES (?, ?, ?, ?)",
            ("old-id", job_id, "collected", "/old/absolute/path/collected/123/1"),
        )
        self.db.conn.commit()

        exports = self.db.get_exports(job_id)
        self.assertEqual(len(exports), 1)
        self.assertEqual(exports[0]["file_path"], "/old/absolute/path/collected/123/1")

    def test_mixed_old_and_new_paths(self):
        """Микс старых абсолютных и новых относительных путей."""
        job_id = self._create_job()

        # Старая абсолютная запись
        self.db.conn.execute(
            "INSERT INTO exports (id, job_id, export_type, file_path) VALUES (?, ?, ?, ?)",
            ("old-id", job_id, "transcript", "/old/path/transcript.pdf"),
        )
        self.db.conn.commit()

        # Новая относительная запись (через save_export)
        self.db.save_export(job_id, "collected", "/new/output/collected/123/1")

        exports = self.db.get_exports(job_id)
        self.assertEqual(len(exports), 2)

        # Старая — абсолютная
        old = next(e for e in exports if e["export_type"] == "transcript")
        self.assertEqual(old["file_path"], "/old/path/transcript.pdf")

        # Новая — резолвится через output_dir
        new = next(e for e in exports if e["export_type"] == "collected")
        self.assertEqual(new["file_path"], "/new/output/collected/123/1")


class TestOutputDirChange(unittest.TestCase):
    """Тесты для смены OUTPUT_DIR: новые записи корректны после смены."""

    def test_new_records_work_with_changed_output_dir(self):
        """Записи, созданные с новым output_dir, резолвятся правильно."""
        db = Database(":memory:", output_dir="/path/v1")
        db.connect()
        db.migrate()

        from app.utils.url_parser import TelegramLink
        link = TelegramLink(chat_id=123, msg_id=1, raw_url="https://t.me/c/123/1")
        job_id = db.create_job(link)

        # Сохраняем с v1
        db.save_export(job_id, "collected", "/path/v1/collected/123/1")

        # Проверяем сырое значение
        raw = db.conn.execute(
            "SELECT file_path FROM exports WHERE job_id = ?", (job_id,)
        ).fetchone()[0]
        self.assertEqual(raw, "collected/123/1")

        # Меняем output_dir на v2
        db.output_dir = "/path/v2"
        exports = db.get_exports(job_id)
        # Теперь путь резолвится через новый output_dir
        self.assertEqual(exports[0]["file_path"], "/path/v2/collected/123/1")

        db.close()


if __name__ == "__main__":
    unittest.main()
