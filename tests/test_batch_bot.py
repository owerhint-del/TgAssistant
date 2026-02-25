"""
Tests for bot /batch command handler.
"""
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.bot.messages import MSG_BATCH_HELP, MSG_BATCH_NO_URLS, MSG_BATCH_STARTED


class TestBatchHandler(unittest.IsolatedAsyncioTestCase):
    """Тесты для /batch handler."""

    def _make_message(self, text):
        msg = AsyncMock()
        msg.text = text
        msg.chat = MagicMock()
        msg.chat.id = 123
        msg.answer = AsyncMock()
        # answer возвращает мок-сообщение с message_id
        status_msg = MagicMock()
        status_msg.message_id = 42
        msg.answer.return_value = status_msg
        return msg

    def _make_bot(self):
        bot = MagicMock()
        bot._db = MagicMock()
        bot._pipeline_runner = MagicMock()
        return bot

    async def test_batch_no_text_shows_help(self):
        """'/batch' без текста → help message."""
        from app.bot.handlers import cmd_batch

        msg = self._make_message("/batch")
        bot = self._make_bot()

        await cmd_batch(msg, bot)
        msg.answer.assert_called_once_with(MSG_BATCH_HELP)

    async def test_batch_empty_text_shows_help(self):
        """'/batch  ' (пробелы) → help message."""
        from app.bot.handlers import cmd_batch

        msg = self._make_message("/batch   ")
        bot = self._make_bot()

        await cmd_batch(msg, bot)
        msg.answer.assert_called_once_with(MSG_BATCH_HELP)

    async def test_batch_no_urls_shows_error(self):
        """'/batch просто текст' без URL → ошибка."""
        from app.bot.handlers import cmd_batch

        msg = self._make_message("/batch Просто текст без ссылок\nЕщё строка")
        bot = self._make_bot()

        await cmd_batch(msg, bot)
        msg.answer.assert_called_once_with(MSG_BATCH_NO_URLS)

    @patch("app.bot.handlers.asyncio")
    async def test_batch_with_urls_calls_submit(self, mock_asyncio):
        """'/batch Тема\\nhttps://example.com' → вызывает submit_batch."""
        from app.bot.handlers import cmd_batch

        mock_loop = MagicMock()
        mock_asyncio.get_running_loop.return_value = mock_loop

        msg = self._make_message("/batch Моя тема\nhttps://example.com/page - Описание")
        bot = self._make_bot()
        runner = bot._pipeline_runner

        await cmd_batch(msg, bot)

        # Статусное сообщение отправлено
        self.assertEqual(msg.answer.call_count, 1)
        call_text = msg.answer.call_args[0][0]
        self.assertIn("Моя тема", call_text)
        self.assertIn("1", call_text)  # count=1

        # submit_batch вызван
        runner.submit_batch.assert_called_once()
        kwargs = runner.submit_batch.call_args
        self.assertEqual(kwargs[1]["chat_id"] if "chat_id" in kwargs[1] else kwargs[0][1], 123)

    @patch("app.bot.handlers.asyncio")
    async def test_batch_multiple_urls(self, mock_asyncio):
        """Заметка с несколькими URL → все передаются в submit_batch."""
        from app.bot.handlers import cmd_batch

        mock_asyncio.get_running_loop.return_value = MagicMock()

        text = "/batch SEO заметки\nhttps://youtube.com/watch?v=abc12345678\nhttps://example.com/page"
        msg = self._make_message(text)
        bot = self._make_bot()

        await cmd_batch(msg, bot)

        # submit_batch вызван
        runner = bot._pipeline_runner
        runner.submit_batch.assert_called_once()
        note = runner.submit_batch.call_args[1].get("note") or runner.submit_batch.call_args[0][0]
        self.assertEqual(note.valid_count, 2)
        self.assertEqual(note.topic, "SEO заметки")


if __name__ == "__main__":
    unittest.main()
