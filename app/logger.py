"""
Настройка логирования с маскировкой секретов.
"""
import logging
import re
import os
from pathlib import Path


# Паттерны для маскировки в логах
_SECRET_PATTERNS = [
    (re.compile(r"(api_hash\s*[=:]\s*)\S+", re.IGNORECASE), r"\1***"),
    (re.compile(r"(api_hash=)[^&\s,\"']+"), r"\1***"),
    (re.compile(r"(sk-ant-)[A-Za-z0-9\-_]+"), r"\1***"),
    (re.compile(r"(\+\d{2,4})\d{4,7}(\d{3})"), r"\1***\2"),  # телефон
    (re.compile(r"(ANTHROPIC_API_KEY\s*[=:]\s*)\S+", re.IGNORECASE), r"\1***"),
    (re.compile(r"SESSION_STRING=\S+", re.IGNORECASE), "SESSION_STRING=[REDACTED]"),
]


class SecretFilter(logging.Filter):
    """Фильтр, маскирующий секреты в сообщениях лога."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _mask(str(record.msg))
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: _mask(str(v)) if isinstance(v, str) else v for k, v in record.args.items()}
            else:
                record.args = tuple(_mask(a) if isinstance(a, str) else a for a in record.args)
        return True


def _mask(text: str) -> str:
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def setup_logger(log_level: str = "INFO", log_dir: str = "./logs") -> logging.Logger:
    """
    Настраивает корневой логгер приложения.
    Выводит в консоль + в файл logs/tgassistant.log.
    """
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_file = os.path.join(log_dir, "tgassistant.log")

    level = getattr(logging, log_level.upper(), logging.INFO)

    logger = logging.getLogger("tgassistant")
    logger.setLevel(level)

    if logger.handlers:
        return logger  # уже настроен

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    secret_filter = SecretFilter()

    # Консоль
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.addFilter(secret_filter)
    logger.addHandler(console)

    # Файл
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(fmt)
    file_handler.addFilter(secret_filter)
    logger.addHandler(file_handler)

    # Приглушить библиотечные логгеры
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    return logger


def get_logger(name: str) -> logging.Logger:
    """Возвращает дочерний логгер."""
    return logging.getLogger(f"tgassistant.{name}")
