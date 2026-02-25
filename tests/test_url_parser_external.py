"""
Тесты парсера внешних ссылок (YouTube, X, VK, Rutube и др.).
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.url_parser import parse_url, TelegramLink, ExternalLink


class TestYouTubeUrls:

    def test_youtube_watch_url(self):
        link = parse_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert isinstance(link, ExternalLink)
        assert link.source == "youtube"
        assert link.video_id == "dQw4w9WgXcQ"

    def test_youtube_short_url(self):
        link = parse_url("https://youtu.be/dQw4w9WgXcQ")
        assert isinstance(link, ExternalLink)
        assert link.source == "youtube"
        assert link.video_id == "dQw4w9WgXcQ"

    def test_youtube_mobile_url(self):
        link = parse_url("https://m.youtube.com/watch?v=dQw4w9WgXcQ")
        assert isinstance(link, ExternalLink)
        assert link.source == "youtube"
        assert link.video_id == "dQw4w9WgXcQ"

    def test_youtube_shorts_url(self):
        link = parse_url("https://www.youtube.com/shorts/abc12345678")
        assert isinstance(link, ExternalLink)
        assert link.source == "youtube"
        assert link.video_id == "abc12345678"

    def test_youtube_embed_url(self):
        link = parse_url("https://www.youtube.com/embed/dQw4w9WgXcQ")
        assert isinstance(link, ExternalLink)
        assert link.source == "youtube"
        assert link.video_id == "dQw4w9WgXcQ"

    def test_youtube_preserves_raw_url(self):
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        link = parse_url(url)
        assert link.raw_url == url

    def test_youtube_with_extra_params(self):
        link = parse_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=120")
        assert isinstance(link, ExternalLink)
        assert link.video_id == "dQw4w9WgXcQ"


class TestXTwitterUrls:

    def test_x_url(self):
        link = parse_url("https://x.com/elonmusk/status/1234567890123456789")
        assert isinstance(link, ExternalLink)
        assert link.source == "x"
        assert link.video_id == "1234567890123456789"

    def test_twitter_url(self):
        link = parse_url("https://twitter.com/elonmusk/status/9876543210")
        assert isinstance(link, ExternalLink)
        assert link.source == "x"
        assert link.video_id == "9876543210"

    def test_twitter_www_url(self):
        link = parse_url("https://www.twitter.com/user/status/111222333")
        assert isinstance(link, ExternalLink)
        assert link.source == "x"
        assert link.video_id == "111222333"

    def test_x_preserves_raw_url(self):
        url = "https://x.com/user/status/123"
        link = parse_url(url)
        assert link.raw_url == url


class TestVkUrls:

    def test_vk_video_url(self):
        link = parse_url("https://vk.com/video-123456_789012")
        assert isinstance(link, ExternalLink)
        assert link.source == "vk"
        assert "video-123456_789012" in link.video_id

    def test_vk_clip_url(self):
        link = parse_url("https://vk.com/clip-123456_789012")
        assert isinstance(link, ExternalLink)
        assert link.source == "vk"

    def test_vk_ru_url(self):
        link = parse_url("https://vk.ru/video-123_456")
        assert isinstance(link, ExternalLink)
        assert link.source == "vk"


class TestRutubeUrls:

    def test_rutube_video_url(self):
        link = parse_url("https://rutube.ru/video/abc123def456/")
        assert isinstance(link, ExternalLink)
        assert link.source == "rutube"
        assert link.video_id == "abc123def456"

    def test_rutube_www_url(self):
        link = parse_url("https://www.rutube.ru/video/abc123def456/")
        assert isinstance(link, ExternalLink)
        assert link.source == "rutube"
        assert link.video_id == "abc123def456"


class TestOtherUrls:

    def test_unknown_video_url(self):
        """URL с неизвестным доменом → ExternalLink(source='other')."""
        link = parse_url("https://rumble.com/some-video.html")
        assert isinstance(link, ExternalLink)
        assert link.source == "other"
        assert len(link.video_id) > 0

    def test_dailymotion_url(self):
        link = parse_url("https://www.dailymotion.com/video/x8abc12")
        assert isinstance(link, ExternalLink)
        assert link.source == "other"


class TestTelegramRegression:

    def test_telegram_private_still_works(self):
        """Telegram-ссылки по-прежнему возвращают TelegramLink."""
        link = parse_url("https://t.me/c/1775135187/1197")
        assert isinstance(link, TelegramLink)
        assert link.chat_id == 1775135187
        assert link.msg_id == 1197

    def test_telegram_public_still_works(self):
        link = parse_url("https://t.me/durov/42")
        assert isinstance(link, TelegramLink)
        assert link.channel_username == "durov"
        assert link.msg_id == 42

    def test_telegram_reserved_path_still_rejected(self):
        with pytest.raises(ValueError, match="зарезервированный путь"):
            parse_url("https://t.me/joinchat/42")


class TestInvalidUrls:

    def test_empty_string(self):
        with pytest.raises(ValueError):
            parse_url("")

    def test_random_text(self):
        with pytest.raises(ValueError):
            parse_url("not a link at all")

    def test_no_scheme(self):
        with pytest.raises(ValueError):
            parse_url("youtube.com/watch?v=abc")
