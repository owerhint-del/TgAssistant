"""
Tests for BatchRunner and IndexBuilder.
Uses mocks to avoid real Telegram/Whisper/yt-dlp calls.
"""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

from app.batch.note_parser import parse_note, NoteEntry, ParsedNote
from app.batch.batch_runner import BatchRunner, BatchResult
from app.batch import index_builder


class TestBatchRunnerAllExternal(unittest.TestCase):
    """Все ссылки внешние → Worker.process мокается → все успешно."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = MagicMock()
        self.cfg.output_dir = self.tmp
        self.cfg.max_retries = 1
        self.cfg.retry_backoff_sec = 0
        self.db = MagicMock()
        self.db.get_job_by_url.return_value = None
        self.db.create_external_job.side_effect = lambda link: f"job-{link.video_id}"
        self.db.get_exports.return_value = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @patch("app.batch.batch_runner.Worker")
    def test_all_succeed(self, MockWorker):
        """Все 3 ссылки обрабатываются успешно."""
        # Настраиваем мок Worker
        mock_worker = MockWorker.return_value
        mock_worker.process.return_value = {"collected_dir": f"{self.tmp}/fake_artifacts"}

        # Создаём папку артефактов
        os.makedirs(f"{self.tmp}/fake_artifacts", exist_ok=True)

        note = parse_note(
            "Test topic\n"
            "https://youtube.com/watch?v=abc12345678 - Video 1\n"
            "https://youtube.com/watch?v=def12345678 - Video 2\n"
            "https://example.com/page - Other link\n"
        )

        runner = BatchRunner(self.cfg, self.db)
        result = runner.run(note)

        self.assertEqual(result.topic, "Test topic")
        self.assertEqual(result.total, 3)
        self.assertEqual(result.succeeded, 3)
        self.assertEqual(result.failed, 0)
        self.assertIsNotNone(result.topic_dir)

        # Worker.process вызван 3 раза
        self.assertEqual(mock_worker.process.call_count, 3)

    @patch("app.batch.batch_runner.Worker")
    def test_topic_dir_created(self, MockWorker):
        """Проверяем структуру topic_dir."""
        mock_worker = MockWorker.return_value
        mock_worker.process.return_value = {"collected_dir": f"{self.tmp}/arts"}
        os.makedirs(f"{self.tmp}/arts", exist_ok=True)

        note = parse_note("My Topic\nhttps://example.com/1 - First")
        runner = BatchRunner(self.cfg, self.db)
        result = runner.run(note)

        topic_dir = Path(result.topic_dir)
        self.assertTrue(topic_dir.exists())
        self.assertTrue((topic_dir / "INDEX.md").exists())
        self.assertTrue((topic_dir / "index.json").exists())

        # Подпапка с source_url.txt
        subdirs = [d for d in topic_dir.iterdir() if d.is_dir()]
        self.assertEqual(len(subdirs), 1)
        self.assertTrue((subdirs[0] / "source_url.txt").exists())

    @patch("app.batch.batch_runner.Worker")
    def test_index_json_structure(self, MockWorker):
        """Проверяем содержимое index.json."""
        mock_worker = MockWorker.return_value
        mock_worker.process.return_value = {"collected_dir": f"{self.tmp}/arts"}
        os.makedirs(f"{self.tmp}/arts", exist_ok=True)

        note = parse_note("JSON Test\nhttps://example.com/1 - Link 1")
        runner = BatchRunner(self.cfg, self.db)
        result = runner.run(note)

        with open(Path(result.topic_dir) / "index.json") as f:
            data = json.load(f)

        self.assertEqual(data["version"], 1)
        self.assertEqual(data["topic"], "JSON Test")
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["succeeded"], 1)
        self.assertEqual(len(data["groups"]), 1)
        self.assertEqual(data["groups"][0]["entries"][0]["status"], "done")


class TestBatchRunnerPartialFailure(unittest.TestCase):
    """Частичный сбой: элемент 2 из 3 падает."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = MagicMock()
        self.cfg.output_dir = self.tmp
        self.cfg.max_retries = 1
        self.cfg.retry_backoff_sec = 0
        self.db = MagicMock()
        self.db.get_job_by_url.return_value = None
        self.db.create_external_job.side_effect = lambda link: f"job-{link.video_id}"
        self.db.get_job_by_id.return_value = {"last_error": "Download failed"}
        self.db.get_exports.return_value = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @patch("app.batch.batch_runner.Worker")
    def test_partial_failure(self, MockWorker):
        """Элемент 2 падает, 1 и 3 — ок."""
        mock_worker = MockWorker.return_value

        call_count = [0]
        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 2:
                return None  # Второй вызов — ошибка
            return {"collected_dir": f"{self.tmp}/arts"}

        mock_worker.process.side_effect = side_effect
        os.makedirs(f"{self.tmp}/arts", exist_ok=True)

        note = parse_note(
            "Partial\n"
            "https://example.com/1 - ok1\n"
            "https://example.com/2 - fail\n"
            "https://example.com/3 - ok3\n"
        )

        runner = BatchRunner(self.cfg, self.db)
        result = runner.run(note)

        self.assertEqual(result.total, 3)
        self.assertEqual(result.succeeded, 2)
        self.assertEqual(result.failed, 1)
        self.assertTrue(result.items[0].success)
        self.assertFalse(result.items[1].success)
        self.assertTrue(result.items[2].success)

    @patch("app.batch.batch_runner.Worker")
    def test_error_entry_has_folder_without_artifacts(self, MockWorker):
        """Ошибочная запись имеет папку с source_url.txt, но без artifacts."""
        mock_worker = MockWorker.return_value
        mock_worker.process.return_value = None

        note = parse_note("Errors\nhttps://example.com/1 - will fail")
        runner = BatchRunner(self.cfg, self.db)
        result = runner.run(note)

        topic_dir = Path(result.topic_dir)
        subdirs = [d for d in topic_dir.iterdir() if d.is_dir()]
        self.assertEqual(len(subdirs), 1)
        self.assertTrue((subdirs[0] / "source_url.txt").exists())
        self.assertFalse((subdirs[0] / "artifacts").exists())


class TestBatchRunnerIdempotency(unittest.TestCase):
    """Идемпотентность: уже обработанные задачи пропускаются."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = MagicMock()
        self.cfg.output_dir = self.tmp
        self.cfg.max_retries = 1
        self.cfg.retry_backoff_sec = 0
        self.db = MagicMock()
        self.db.get_exports.return_value = [
            {"export_type": "collected", "file_path": f"{self.tmp}/existing_arts"}
        ]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @patch("app.batch.batch_runner.Worker")
    def test_skip_done_job(self, MockWorker):
        """Уже готовая задача не вызывает Worker.process()."""
        mock_worker = MockWorker.return_value

        self.db.get_job_by_url.return_value = {
            "id": "existing-job",
            "status": "done",
        }

        note = parse_note("Idempotent\nhttps://example.com/already-done")
        runner = BatchRunner(self.cfg, self.db)
        result = runner.run(note)

        self.assertEqual(result.succeeded, 1)
        mock_worker.process.assert_not_called()
        self.assertEqual(result.items[0].job_id, "existing-job")

    @patch("app.batch.batch_runner.Worker")
    def test_from_start_reprocesses(self, MockWorker):
        """from_start=True переобрабатывает даже done задачи."""
        mock_worker = MockWorker.return_value
        mock_worker.process.return_value = {"collected_dir": f"{self.tmp}/arts"}
        os.makedirs(f"{self.tmp}/arts", exist_ok=True)

        self.db.get_job_by_url.return_value = {
            "id": "existing-job",
            "status": "done",
        }

        note = parse_note("Reprocess\nhttps://example.com/redo")
        runner = BatchRunner(self.cfg, self.db)
        result = runner.run(note, from_start=True)

        self.assertEqual(result.succeeded, 1)
        mock_worker.process.assert_called_once()


class TestBatchRunnerEmptyBatch(unittest.TestCase):
    """Пустой батч: 0 валидных URL → без обработки."""

    def setUp(self):
        self.cfg = MagicMock()
        self.cfg.output_dir = tempfile.mkdtemp()
        self.db = MagicMock()

    def tearDown(self):
        shutil.rmtree(self.cfg.output_dir, ignore_errors=True)

    @patch("app.batch.batch_runner.Worker")
    def test_empty_batch(self, MockWorker):
        """Заметка без URL → нет обработки, нет topic_dir."""
        note = parse_note("Just a title\nno links here")
        runner = BatchRunner(self.cfg, self.db)
        result = runner.run(note)

        self.assertEqual(result.total, 0)
        self.assertIsNone(result.topic_dir)
        MockWorker.return_value.process.assert_not_called()


class TestIndexBuilder(unittest.TestCase):
    """Тесты IndexBuilder отдельно от BatchRunner."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_item(self, index, url, label, group="", success=True, artifact_dir=None):
        entry = NoteEntry(url=url, label=label, group=group, line_number=index)
        item = MagicMock()
        item.entry = entry
        item.index = index
        item.success = success
        item.error = None if success else "Some error"
        item.artifact_dir = artifact_dir
        return item

    def test_basic_index_md(self):
        """INDEX.md создаётся с правильной таблицей."""
        art_dir = Path(self.tmp) / "fake_art"
        art_dir.mkdir()

        items = [
            self._make_item(1, "https://example.com/1", "Link one", artifact_dir=str(art_dir)),
            self._make_item(2, "https://example.com/2", "Link two", success=False),
        ]

        topic_dir = index_builder.build("Test Topic", items, self.tmp)

        md = (Path(topic_dir) / "INDEX.md").read_text()
        self.assertIn("# Test Topic", md)
        self.assertIn("1/2 succeeded", md)
        self.assertIn("Link one", md)
        self.assertIn("Link two", md)
        self.assertIn("done", md)
        self.assertIn("error", md)

    def test_symlink_created(self):
        """Симлинк на артефакты создаётся."""
        art_dir = Path(self.tmp) / "source_artifacts"
        art_dir.mkdir()
        (art_dir / "test.txt").write_text("hello")

        items = [
            self._make_item(1, "https://example.com/1", "Link", artifact_dir=str(art_dir)),
        ]

        topic_dir = index_builder.build("Symlink Test", items, self.tmp)
        entry_dir = next(d for d in Path(topic_dir).iterdir() if d.is_dir())
        artifacts = entry_dir / "artifacts"
        self.assertTrue(artifacts.is_symlink())
        self.assertTrue((artifacts / "test.txt").exists())

    def test_copy_mode(self):
        """use_symlinks=False → копируются артефакты."""
        art_dir = Path(self.tmp) / "source_copy"
        art_dir.mkdir()
        (art_dir / "test.txt").write_text("hello")

        items = [
            self._make_item(1, "https://example.com/1", "Link", artifact_dir=str(art_dir)),
        ]

        topic_dir = index_builder.build("Copy Test", items, self.tmp, use_symlinks=False)
        entry_dir = next(d for d in Path(topic_dir).iterdir() if d.is_dir())
        artifacts = entry_dir / "artifacts"
        self.assertFalse(artifacts.is_symlink())
        self.assertTrue((artifacts / "test.txt").exists())

    def test_groups_in_index(self):
        """Группы отображаются в INDEX.md и index.json."""
        items = [
            self._make_item(1, "https://example.com/1", "Fire link", group="\U0001f525"),
            self._make_item(2, "https://example.com/2", "Arrow link", group="->"),
        ]

        topic_dir = index_builder.build("Groups", items, self.tmp)

        with open(Path(topic_dir) / "index.json") as f:
            data = json.load(f)

        self.assertEqual(len(data["groups"]), 2)
        self.assertEqual(data["groups"][0]["prefix"], "\U0001f525")
        self.assertEqual(data["groups"][1]["prefix"], "->")

    def test_numbered_folders(self):
        """Подпапки пронумерованы: 01_, 02_."""
        items = [
            self._make_item(1, "https://example.com/1", "First"),
            self._make_item(2, "https://example.com/2", "Second"),
        ]

        topic_dir = index_builder.build("Numbered", items, self.tmp)
        folders = sorted(d.name for d in Path(topic_dir).iterdir() if d.is_dir())
        self.assertTrue(folders[0].startswith("01_"))
        self.assertTrue(folders[1].startswith("02_"))


if __name__ == "__main__":
    unittest.main()
