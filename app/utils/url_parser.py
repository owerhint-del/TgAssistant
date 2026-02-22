"""
Парсинг ссылок вида https://t.me/c/<chat_id>/<msg_id>
"""
import re
from dataclasses import dataclass


@dataclass
class TelegramLink:
    chat_id: int    # ID канала (без -100 префикса)
    msg_id: int     # ID сообщения
    raw_url: str    # исходная ссылка


# Поддерживаемые форматы:
# https://t.me/c/1234567890/42
# https://t.me/c/1234567890/42?single  (игнорируем ?single)
_PRIVATE_PATTERN = re.compile(
    r"https?://t\.me/c/(\d+)/(\d+)"
)


def parse_url(url: str) -> TelegramLink:
    """
    Разбирает ссылку на приватный Telegram-пост.

    Args:
        url: ссылка вида https://t.me/c/<chat_id>/<msg_id>

    Returns:
        TelegramLink с chat_id и msg_id

    Raises:
        ValueError: если формат ссылки неверный
    """
    url = url.strip()
    match = _PRIVATE_PATTERN.search(url)
    if not match:
        raise ValueError(
            f"Неверный формат ссылки: {url!r}\n"
            "Ожидается формат: https://t.me/c/<chat_id>/<msg_id>\n"
            "Пример: https://t.me/c/1775135187/1197"
        )

    # Проверяем что совпадение находится в корне URL, а не внутри другого URL
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
