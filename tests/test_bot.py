"""
Tests for Telegram bot: middleware, handlers, progress callback.
"""
import asyncio
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.config import Config
from app.bot.messages import (
    MSG_UNAUTHORIZED,
    MSG_NOT_A_LINK,
    MSG_START,
    MSG_HELP,
    MSG_PROCESSING_STARTED,
    MSG_ALREADY_DONE,
    MSG_ALREADY_PROCESSING,
    MSG_INVALID_URL,
    STATUS_MESSAGES,
)


class TestAdminOnlyMiddleware(unittest.IsolatedAsyncioTestCase):
    """AdminOnlyMiddleware должен пропускать только admin_ids."""

    def _make_middleware(self, admin_ids):
        from app.bot.middleware import AdminOnlyMiddleware
        return AdminOnlyMiddleware(admin_ids)

    def _make_message(self, user_id):
        msg = AsyncMock()
        msg.from_user = MagicMock()
        msg.from_user.id = user_id
        msg.answer = AsyncMock()
        return msg

    async def test_admin_allowed(self):
        mw = self._make_middleware([123, 456])
        handler = AsyncMock(return_value="ok")
        msg = self._make_message(123)

        result = await mw(handler, msg, {})
        handler.assert_called_once_with(msg, {})
        msg.answer.assert_not_called()
        self.assertEqual(result, "ok")

    async def test_non_admin_blocked(self):
        mw = self._make_middleware([123])
        handler = AsyncMock()
        msg = self._make_message(999)

        result = await mw(handler, msg, {})
        handler.assert_not_called()
        msg.answer.assert_called_once_with(MSG_UNAUTHORIZED)
        self.assertIsNone(result)

    async def test_no_user_blocked(self):
        mw = self._make_middleware([123])
        handler = AsyncMock()
        msg = AsyncMock()
        msg.from_user = None
        msg.answer = AsyncMock()

        result = await mw(handler, msg, {})
        handler.assert_not_called()
        msg.answer.assert_called_once_with(MSG_UNAUTHORIZED)

    async def test_empty_admin_list_blocks_all(self):
        mw = self._make_middleware([])
        handler = AsyncMock()
        msg = self._make_message(123)

        result = await mw(handler, msg, {})
        handler.assert_not_called()


class TestBotProgressCallback(unittest.TestCase):
    """BotProgressCallback: throttle, dedup, thread-safe edit."""

    def _make_callback(self):
        from app.bot.progress import BotProgressCallback

        bot = MagicMock()
        # edit_message_text returns a coroutine
        bot.edit_message_text = AsyncMock()

        loop = asyncio.new_event_loop()
        cb = BotProgressCallback(
            bot=bot,
            chat_id=100,
            message_id=200,
            loop=loop,
        )
        return cb, bot, loop

    def tearDown(self):
        # Clean up any event loops
        pass

    def test_dedup_same_status(self):
        """Повторный вызов с тем же статусом — пропускается."""
        cb, bot, loop = self._make_callback()

        # Запускаем loop в отдельном потоке
        import threading
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()

        try:
            cb("job1", "downloading")
            time.sleep(0.1)
            call_count_1 = bot.edit_message_text.call_count

            cb("job1", "downloading")
            time.sleep(0.1)
            call_count_2 = bot.edit_message_text.call_count

            # Второй вызов не должен был сработать
            self.assertEqual(call_count_1, call_count_2)
        finally:
            loop.call_soon_threadsafe(loop.stop)
            t.join(timeout=2)
            loop.close()

    def test_different_status_goes_through(self):
        """Разные статусы — оба проходят."""
        cb, bot, loop = self._make_callback()

        import threading
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()

        try:
            cb("job1", "downloading")
            time.sleep(0.1)

            # Сбрасываем throttle для теста
            cb._last_edit_time = 0

            cb("job1", "transcribing")
            time.sleep(0.1)

            self.assertEqual(bot.edit_message_text.call_count, 2)
        finally:
            loop.call_soon_threadsafe(loop.stop)
            t.join(timeout=2)
            loop.close()

    def test_done_bypasses_throttle(self):
        """Статус 'done' всегда проходит, даже при throttle."""
        cb, bot, loop = self._make_callback()

        import threading
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()

        try:
            cb("job1", "downloading")
            time.sleep(0.1)
            # Не сбрасываем throttle — done должен пройти всё равно
            cb("job1", "done")
            time.sleep(0.1)

            self.assertEqual(bot.edit_message_text.call_count, 2)
        finally:
            loop.call_soon_threadsafe(loop.stop)
            t.join(timeout=2)
            loop.close()

    def test_error_bypasses_throttle(self):
        """Статус 'error' всегда проходит."""
        cb, bot, loop = self._make_callback()

        import threading
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()

        try:
            cb("job1", "downloading")
            time.sleep(0.1)
            cb("job1", "error")
            time.sleep(0.1)

            self.assertEqual(bot.edit_message_text.call_count, 2)
        finally:
            loop.call_soon_threadsafe(loop.stop)
            t.join(timeout=2)
            loop.close()


class TestStatusMessages(unittest.TestCase):
    """Проверяем полноту STATUS_MESSAGES."""

    def test_all_pipeline_statuses_covered(self):
        expected = {"pending", "analyzing", "downloading", "transcribing",
                    "exporting", "collecting", "saving", "done", "error"}
        self.assertTrue(expected.issubset(STATUS_MESSAGES.keys()))


class TestConfigBotFields(unittest.TestCase):
    """Config должен загружать bot_token и bot_admin_ids."""

    def test_default_values(self):
        cfg = Config()
        self.assertEqual(cfg.bot_token, "")
        self.assertEqual(cfg.bot_admin_ids, [])

    @patch.dict("os.environ", {"TG_BOT_TOKEN": "test:token123", "TG_BOT_ADMIN_IDS": "111,222,333"})
    def test_env_loading(self):
        from app.config import load_config
        cfg = load_config()
        self.assertEqual(cfg.bot_token, "test:token123")
        self.assertEqual(cfg.bot_admin_ids, [111, 222, 333])

    @patch.dict("os.environ", {"TG_BOT_TOKEN": "test:token", "TG_BOT_ADMIN_IDS": ""})
    def test_empty_admin_ids(self):
        from app.config import load_config
        cfg = load_config()
        self.assertEqual(cfg.bot_admin_ids, [])


if __name__ == "__main__":
    unittest.main()
