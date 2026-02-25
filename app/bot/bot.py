"""
Точка входа Telegram-бота.
Настраивает Dispatcher, middleware, handlers и запускает polling.
"""
import logging

from aiogram import Bot, Dispatcher

from app.config import Config
from app.db.database import Database
from app.bot.middleware import AdminOnlyMiddleware
from app.bot.runner import BotPipelineRunner
from app.bot.handlers import router as handlers_router
from app.bot.messages import MSG_NO_TOKEN

logger = logging.getLogger("tgassistant.bot")


async def run_bot(cfg: Config, db: Database) -> None:
    """Запускает Telegram-бота в режиме long-polling."""
    if not cfg.bot_token:
        print(f"\n  ✗ {MSG_NO_TOKEN}")
        print("  Задай TG_BOT_TOKEN в .env или config.yaml")
        return

    if not cfg.bot_admin_ids:
        print("\n  ⚠ TG_BOT_ADMIN_IDS не задан — бот не будет отвечать никому.")
        print("  Задай TG_BOT_ADMIN_IDS=<твой_telegram_id> в .env или config.yaml")
        return

    bot = Bot(token=cfg.bot_token)
    dp = Dispatcher()

    # Middleware: только админы
    dp.message.middleware(AdminOnlyMiddleware(cfg.bot_admin_ids))

    # Pipeline runner
    runner = BotPipelineRunner(cfg, db, bot)

    # Прикрепляем к bot для доступа из handlers
    bot._db = db
    bot._pipeline_runner = runner

    # Подключаем роутер с обработчиками
    dp.include_router(handlers_router)

    print("\n  ✓ TgAssistant Bot запущен!")
    print(f"  Админы: {cfg.bot_admin_ids}")
    print("  Для остановки нажми Ctrl+C\n")

    logger.info("Bot started. Admin IDs: %s", cfg.bot_admin_ids)

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
