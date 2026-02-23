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

    def test_public_channel_link(self):
        link = parse_url("https://t.me/somechannel/42")
        assert link.chat_id == 0
        assert link.msg_id == 42
        assert link.channel_username == "somechannel"

    def test_public_channel_preserves_raw_url(self):
        url = "https://t.me/durov/123"
        link = parse_url(url)
        assert link.raw_url == url
        assert link.channel_username == "durov"
        assert link.msg_id == 123

    def test_public_channel_with_query(self):
        link = parse_url("https://t.me/mychannel/99?single")
        assert link.channel_username == "mychannel"
        assert link.msg_id == 99

    def test_reserved_path_rejected(self):
        with pytest.raises(ValueError, match="зарезервированный путь"):
            parse_url("https://t.me/joinchat/42")

    def test_private_link_has_no_username(self):
        link = parse_url("https://t.me/c/1234567890/42")
        assert link.channel_username is None

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
