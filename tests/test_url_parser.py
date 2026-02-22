"""
Тесты парсера Telegram-ссылок.
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.url_parser import parse_url, TelegramLink


class TestParseUrl:

    def test_standard_link(self):
        link = parse_url("https://t.me/c/1775135187/1197")
        assert link.chat_id == 1775135187
        assert link.msg_id == 1197
        assert link.raw_url == "https://t.me/c/1775135187/1197"

    def test_link_with_http(self):
        link = parse_url("http://t.me/c/1234567890/42")
        assert link.chat_id == 1234567890
        assert link.msg_id == 42

    def test_link_with_trailing_spaces(self):
        link = parse_url("  https://t.me/c/1775135187/1197  ")
        assert link.chat_id == 1775135187

    def test_link_with_query_param(self):
        link = parse_url("https://t.me/c/1775135187/1197?single")
        assert link.chat_id == 1775135187
        assert link.msg_id == 1197

    def test_invalid_link_public_channel(self):
        with pytest.raises(ValueError, match="Неверный формат"):
            parse_url("https://t.me/somechannel/42")

    def test_invalid_link_empty(self):
        with pytest.raises(ValueError):
            parse_url("")

    def test_invalid_link_random_text(self):
        with pytest.raises(ValueError):
            parse_url("not a link at all")

    def test_invalid_link_missing_msg_id(self):
        with pytest.raises(ValueError):
            parse_url("https://t.me/c/1775135187")

    def test_large_ids(self):
        link = parse_url("https://t.me/c/9999999999/99999")
        assert link.chat_id == 9999999999
        assert link.msg_id == 99999

    def test_returns_telegram_link_type(self):
        result = parse_url("https://t.me/c/1234/56")
        assert isinstance(result, TelegramLink)
