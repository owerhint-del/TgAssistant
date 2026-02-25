"""
Парсинг ссылок вида:
  - https://t.me/c/<chat_id>/<msg_id>       (приватный канал)
  - https://t.me/<channel_username>/<msg_id> (публичный канал)
  - https://youtube.com/watch?v=xxx          (YouTube)
  - https://x.com/user/status/123            (X / Twitter)
  - и другие внешние видео-платформы (yt-dlp)
"""
import hashlib
import re
from dataclasses import dataclass, field
from typing import Optional, Union
from urllib.parse import urlparse, parse_qs


@dataclass
class TelegramLink:
    chat_id: int    # ID канала (без -100 префикса), 0 для публичных каналов
    msg_id: int     # ID сообщения
    raw_url: str    # исходная ссылка
    channel_username: Optional[str] = None  # username публичного канала (без @)


@dataclass
class ExternalLink:
    source: str      # "youtube", "x", "vk", "rutube", "other"
    video_id: str    # platform-specific ID
    raw_url: str     # original URL


# Union type for type hints
ParsedLink = Union[TelegramLink, ExternalLink]


# Приватный канал: https://t.me/c/1234567890/42
_PRIVATE_PATTERN = re.compile(
    r"https?://t\.me/c/(\d+)/(\d+)"
)

# Публичный канал: https://t.me/channelname/42
# username: 5-32 символа, латиница/цифры/подчёркивания, не начинается с цифры
_PUBLIC_PATTERN = re.compile(
    r"https?://t\.me/([a-zA-Z_][a-zA-Z0-9_]{4,31})/(\d+)"
)

# Зарезервированные пути t.me (не username каналов)
_RESERVED_PATHS = frozenset({
    "c", "s", "joinchat", "addstickers", "addemoji", "setlanguage",
    "share", "login", "socks", "proxy", "bg", "addtheme", "confirmphone",
    "invoice", "addlist",
})


def _parse_telegram_url(url: str) -> Optional[TelegramLink]:
    """
    Пробует распарсить URL как Telegram-ссылку.
    Возвращает TelegramLink или None если не подходит.
    Raises ValueError для недопустимых Telegram-ссылок.
    """
    # Сначала пробуем приватный формат
    match = _PRIVATE_PATTERN.search(url)
    if match:
        match_start = match.start()
        if match_start > 0 and url[:match_start].rstrip().endswith(("=", "?", "&")):
            raise ValueError(
                f"Неверный формат ссылки — ссылка найдена внутри другого URL: {url!r}"
            )

        chat_id = int(match.group(1))
        msg_id = int(match.group(2))

        if chat_id == 0:
            raise ValueError("chat_id не может быть 0. Проверь ссылку.")
        if msg_id == 0:
            raise ValueError("msg_id не может быть 0. Проверь ссылку.")

        return TelegramLink(chat_id=chat_id, msg_id=msg_id, raw_url=url)

    # Пробуем публичный формат
    match = _PUBLIC_PATTERN.search(url)
    if match:
        username = match.group(1)
        if username.lower() in _RESERVED_PATHS:
            raise ValueError(
                f"Неверный формат ссылки: {url!r}\n"
                f"'{username}' — зарезервированный путь Telegram, не username канала."
            )

        match_start = match.start()
        if match_start > 0 and url[:match_start].rstrip().endswith(("=", "?", "&")):
            raise ValueError(
                f"Неверный формат ссылки — ссылка найдена внутри другого URL: {url!r}"
            )

        msg_id = int(match.group(2))
        if msg_id == 0:
            raise ValueError("msg_id не может быть 0. Проверь ссылку.")

        return TelegramLink(
            chat_id=0,
            msg_id=msg_id,
            raw_url=url,
            channel_username=username,
        )

    return None


# ─── Маппинг доменов на источники ─────────────────────────────
_DOMAIN_SOURCE_MAP = {
    "youtube.com": "youtube",
    "www.youtube.com": "youtube",
    "m.youtube.com": "youtube",
    "youtu.be": "youtube",
    "x.com": "x",
    "www.x.com": "x",
    "twitter.com": "x",
    "www.twitter.com": "x",
    "mobile.twitter.com": "x",
    "vk.com": "vk",
    "www.vk.com": "vk",
    "vk.ru": "vk",
    "www.vk.ru": "vk",
    "rutube.ru": "rutube",
    "www.rutube.ru": "rutube",
}

# YouTube video ID pattern
_YT_VIDEO_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{11}$")

# X/Twitter status ID pattern
_X_STATUS_PATTERN = re.compile(r"/status/(\d+)")


def _extract_video_id(source: str, url: str, parsed: "urlparse") -> str:
    """Извлекает platform-specific video ID из URL."""
    if source == "youtube":
        # youtube.com/watch?v=xxx
        qs = parse_qs(parsed.query)
        if "v" in qs:
            vid = qs["v"][0]
            if _YT_VIDEO_ID_PATTERN.match(vid):
                return vid
        # youtu.be/xxx
        if parsed.hostname in ("youtu.be",):
            path = parsed.path.lstrip("/")
            if path and _YT_VIDEO_ID_PATTERN.match(path.split("/")[0]):
                return path.split("/")[0]
        # youtube.com/shorts/xxx, youtube.com/embed/xxx, youtube.com/v/xxx
        for prefix in ("/shorts/", "/embed/", "/v/"):
            if parsed.path.startswith(prefix):
                vid = parsed.path[len(prefix):].split("/")[0].split("?")[0]
                if _YT_VIDEO_ID_PATTERN.match(vid):
                    return vid
        # Fallback: use URL hash
        return hashlib.md5(url.encode()).hexdigest()[:12]

    if source == "x":
        match = _X_STATUS_PATTERN.search(parsed.path)
        if match:
            return match.group(1)
        return hashlib.md5(url.encode()).hexdigest()[:12]

    if source == "rutube":
        # rutube.ru/video/<id>/
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2 and parts[0] == "video":
            return parts[1]
        return hashlib.md5(url.encode()).hexdigest()[:12]

    if source == "vk":
        # vk.com/video-123_456 or vk.com/clip-123_456
        path = parsed.path.lstrip("/")
        if path:
            return path.replace("/", "_")
        return hashlib.md5(url.encode()).hexdigest()[:12]

    # "other" source
    return hashlib.md5(url.encode()).hexdigest()[:12]


def _parse_external_url(url: str) -> Optional[ExternalLink]:
    """
    Пробует распарсить URL как внешнюю видео-ссылку.
    Возвращает ExternalLink или None если не похоже на URL.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return None

    # Нужен хотя бы хост и схема
    if not parsed.hostname or parsed.scheme not in ("http", "https"):
        return None

    hostname = parsed.hostname.lower()

    # Не обрабатываем Telegram-ссылки здесь
    if hostname in ("t.me", "telegram.me"):
        return None

    # Определяем source по домену
    source = _DOMAIN_SOURCE_MAP.get(hostname, "other")

    video_id = _extract_video_id(source, url, parsed)

    return ExternalLink(source=source, video_id=video_id, raw_url=url)


def parse_url(url: str) -> ParsedLink:
    """
    Разбирает ссылку: Telegram или внешняя видео-платформа.

    Args:
        url: ссылка — Telegram, YouTube, X, VK, Rutube и др.

    Returns:
        TelegramLink или ExternalLink

    Raises:
        ValueError: если формат ссылки неверный
    """
    url = url.strip()

    # Сначала пробуем Telegram (может бросить ValueError для плохих TG-ссылок)
    try:
        tg_link = _parse_telegram_url(url)
        if tg_link is not None:
            return tg_link
    except ValueError:
        raise  # Плохая Telegram-ссылка — пробрасываем

    # Пробуем внешнюю ссылку
    ext_link = _parse_external_url(url)
    if ext_link is not None:
        return ext_link

    raise ValueError(
        f"Неподдерживаемый формат ссылки: {url!r}\n"
        "Ожидается формат:\n"
        "  Telegram:  https://t.me/c/<chat_id>/<msg_id>\n"
        "             https://t.me/<channel_username>/<msg_id>\n"
        "  YouTube:   https://www.youtube.com/watch?v=...\n"
        "  X:         https://x.com/<user>/status/<id>\n"
        "  VK:        https://vk.com/video...\n"
        "  Rutube:    https://rutube.ru/video/<id>/\n"
        "  Другие:    любой URL с http(s)://"
    )
