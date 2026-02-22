"""
Управление Telegram-сессией через Telethon.
Авторизация, проверка сессии, интерактивный логин.
"""
import asyncio
import os
import stat
import logging
from pathlib import Path
from typing import Optional

from telethon import TelegramClient
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    FloodWaitError,
)

from app.config import Config
from app.utils.async_utils import run_sync

logger = logging.getLogger("tgassistant.auth")


def _secure_permissions(path: str) -> None:
    """Устанавливает права 600 на файл сессии."""
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def make_client(cfg: Config) -> TelegramClient:
    """Создаёт Telethon-клиент с настройками из конфига."""
    session_path = str(Path(cfg.tg_session_path).expanduser())
    Path(session_path).parent.mkdir(parents=True, exist_ok=True)

    client = TelegramClient(
        session_path,
        cfg.tg_api_id,
        cfg.tg_api_hash,
        flood_sleep_threshold=60,
        request_retries=5,
        connection_retries=5,
        retry_delay=2,
    )
    return client


async def is_authorized(client: TelegramClient) -> bool:
    """Проверяет, авторизован ли клиент. Гарантированно закрывает соединение."""
    try:
        await client.connect()
        return await client.is_user_authorized()
    except Exception as e:
        logger.debug("Ошибка проверки авторизации: %s", e)
        return False
    finally:
        # Закрываем соединение в любом случае — вызывающий код откроет его заново
        try:
            await client.disconnect()
        except Exception:
            pass


async def interactive_login(cfg: Config) -> TelegramClient:
    """
    Интерактивная авторизация Telegram.
    Запрашивает код из SMS/приложения и 2FA-пароль если нужно.
    Вызывается только из --setup.
    """
    client = make_client(cfg)
    await client.connect()

    if await client.is_user_authorized():
        logger.info("Сессия уже активна, повторная авторизация не нужна.")
        return client

    print(f"\nОтправляю код подтверждения на {cfg.tg_phone}...")
    try:
        sent = await client.send_code_request(cfg.tg_phone)
    except FloodWaitError as e:
        print(f"\nTelegram просит подождать {e.seconds} секунд. Подожди и попробуй снова.")
        raise

    code = input("Введи код из Telegram (цифры): ").strip()

    try:
        await client.sign_in(cfg.tg_phone, code)
    except SessionPasswordNeededError:
        # 2FA активирован
        print("\nОбнаружена двухфакторная аутентификация (2FA).")
        password = input("Введи облачный пароль Telegram: ").strip()
        await client.sign_in(password=password)
    except PhoneCodeInvalidError:
        print("\nНеверный код. Попробуй запустить --setup снова.")
        raise
    except PhoneCodeExpiredError:
        print("\nКод истёк. Попробуй запустить --setup снова.")
        raise

    # Защищаем файл сессии
    session_file = cfg.tg_session_path + ".session"
    _secure_permissions(session_file)

    me = await client.get_me()
    print(f"\nАвторизация успешна! Аккаунт: {me.first_name} ({cfg.tg_phone})")
    return client


def get_authorized_client(cfg: Config) -> TelegramClient:
    """
    Возвращает готовый (авторизованный) Telethon-клиент.
    Если сессия не существует — выбрасывает RuntimeError с инструкцией.
    """
    session_file = Path(cfg.tg_session_path + ".session")
    if not session_file.exists():
        raise RuntimeError(
            "Сессия Telegram не найдена.\n"
            "Запусти первоначальную настройку:\n\n"
            "  python run.py --setup\n"
        )

    client = make_client(cfg)

    authorized = run_sync(is_authorized(client))
    if not authorized:
        raise RuntimeError(
            "Сессия Telegram истекла или недействительна.\n"
            "Запусти повторную настройку:\n\n"
            "  python run.py --setup\n"
        )

    return client
