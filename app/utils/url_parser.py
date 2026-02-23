"""
Парсинг ссылок вида:
  - https://t.me/c/<chat_id>/<msg_id>       (приватный канал)
  - https://t.me/<channel_username>/<msg_id> (публичный канал)
"""
import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TelegramLink:
    chat_id: int    # ID канала (без -100 префикса), 0 для публичных каналов
    msg_id: int     # ID сообщения
    raw_url: str    # исходная ссылка
    channel_username: Optional[str] = None  # username публичного канала (без @)


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


def parse_url(url: str) -> TelegramLink:
    """
    Разбирает ссылку на Telegram-пост (приватный или публичный канал).

    Args:
        url: ссылка вида https://t.me/c/<chat_id>/<msg_id>
             или https://t.me/<username>/<msg_id>

    Returns:
        TelegramLink с chat_id (или channel_username) и msg_id

    Raises:
        ValueError: если формат ссылки неверный
    """
    url = url.strip()

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

    raise ValueError(
        f"Неверный формат ссылки: {url!r}\n"
        "Ожидается формат:\n"
        "  Приватный: https://t.me/c/<chat_id>/<msg_id>\n"
        "  Публичный: https://t.me/<channel_username>/<msg_id>\n"
        "Примеры:\n"
        "  https://t.me/c/1775135187/1197\n"
        "  https://t.me/durov/42"
    )
