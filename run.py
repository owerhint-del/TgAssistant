#!/usr/bin/env python3
"""
TgAssistant — главная точка входа.

Использование:
  python run.py --setup                  # первоначальная настройка
  python run.py --check-config           # проверить конфигурацию
  python run.py --link <url>             # обработать ссылку
  python run.py --watch                  # режим stdin (много ссылок)
  python run.py --batch-file <path>      # пакетная обработка из файла
  python run.py --batch-text <text>      # пакетная обработка из текста
  python run.py --status [--filter done] # история задач
  python run.py --retry <job_id>         # повторить упавшую задачу
"""
import argparse
import asyncio
import sys
from pathlib import Path


def _bootstrap():
    """Проверяет Python-версию и наличие зависимостей."""
    if sys.version_info < (3, 11):
        print(
            f"Ошибка: требуется Python 3.11+, у тебя {sys.version}\n"
            "Установи: brew install python@3.11"
        )
        sys.exit(1)


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python run.py",
        description="TgAssistant — транскрипция Telegram-материалов и внешних видео",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--setup",
        action="store_true",
        help="Первоначальная настройка (телефон, ключи, авторизация)",
    )
    group.add_argument(
        "--check-config",
        action="store_true",
        help="Проверить конфигурацию без запуска обработки",
    )
    group.add_argument(
        "--link",
        nargs="+",
        metavar="URL",
        help="Ссылка(и) для обработки (Telegram, YouTube, X, VK, Rutube и др.)",
    )
    group.add_argument(
        "--watch",
        action="store_true",
        help="Режим stdin: вставляй ссылки и нажимай Enter",
    )
    group.add_argument(
        "--status",
        action="store_true",
        help="Показать историю всех задач",
    )
    group.add_argument(
        "--retry",
        metavar="JOB_ID",
        help="Повторить задачу с последнего успешного шага",
    )
    group.add_argument(
        "--web",
        action="store_true",
        help="Запустить веб-интерфейс (localhost:8000)",
    )
    group.add_argument(
        "--bot",
        action="store_true",
        help="Запустить Telegram-бота",
    )
    group.add_argument(
        "--batch-file",
        metavar="PATH",
        help="Пакетная обработка: путь к файлу с заметкой (тема + ссылки)",
    )
    group.add_argument(
        "--batch-text",
        metavar="TEXT",
        help="Пакетная обработка: текст заметки (тема + ссылки)",
    )
    group.add_argument(
        "--cleanup",
        action="store_true",
        help="Удалить видео/аудио файлы старше N дней в collected/",
    )

    # Общие флаги
    parser.add_argument("--config", metavar="PATH", help="Путь к config.yaml")
    parser.add_argument("--output-dir", metavar="DIR", help="Папка для PDF")
    parser.add_argument("--host", default="127.0.0.1", help="Адрес для --web (по умолчанию 127.0.0.1, для LAN: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Порт для --web (по умолчанию 8000)")
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=None,
        help="Уровень логирования",
    )
    parser.add_argument(
        "--filter",
        choices=["done", "error", "pending", "in_progress"],
        help="Фильтр для --status",
    )
    parser.add_argument(
        "--from-start",
        action="store_true",
        help="При --retry: начать пайплайн с нуля",
    )
    parser.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Не удалять temp файлы после обработки",
    )
    parser.add_argument(
        "--topic",
        metavar="NAME",
        help="Переопределить тему для --batch-file/--batch-text",
    )
    parser.add_argument(
        "--no-symlink",
        action="store_true",
        help="Копировать артефакты вместо симлинков (для --batch)",
    )
    parser.add_argument(
        "--older-than",
        type=int,
        default=7,
        metavar="DAYS",
        help="Для --cleanup: возраст файлов в днях (по умолчанию 7)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Для --cleanup: показать что будет удалено, не удаляя",
    )

    return parser


def _load_app(args):
    """Загружает конфиг и инициализирует приложение."""
    from app.logger import setup_logger
    from app.config import load_config, validate_config

    overrides = {}
    if args.output_dir:
        overrides["output_dir"] = args.output_dir
    if args.log_level:
        overrides["log_level"] = args.log_level
    if getattr(args, "no_cleanup", False):
        overrides["cleanup_temp"] = False

    cfg = load_config(config_file=args.config, overrides=overrides)
    setup_logger(cfg.log_level, cfg.log_dir)

    from app.db.database import Database
    from app.utils.cleanup import cleanup_orphans

    db = Database(cfg.db_path, output_dir=cfg.output_dir)
    db.connect()
    db.migrate()
    cleanup_orphans(cfg.temp_dir, cfg.orphan_retention_hours)

    return cfg, db


def cmd_setup():
    from app.setup_wizard import run_setup
    run_setup()


def cmd_check_config(args):
    from app.config import load_config, validate_config
    from app.logger import setup_logger

    cfg = load_config(config_file=args.config)
    setup_logger("INFO", cfg.log_dir)

    print("\n  Проверка конфигурации TgAssistant\n  " + "─" * 40)

    errors = validate_config(cfg)
    checks = []

    # Telegram
    checks.append(("TG credentials", True, "built-in (override via ENV)"))
    checks.append(("TG_PHONE",      bool(cfg.tg_phone),         cfg.tg_phone or "не задан"))

    session_file = Path(cfg.tg_session_path + ".session")
    checks.append(("Session файл",  session_file.exists(),     str(session_file)))

    # Anthropic (optional — not needed for verbatim transcription)
    checks.append(("ANTHROPIC_KEY", True, "sk-ant-***" if cfg.anthropic_api_key else "(optional)"))

    # ffmpeg
    import subprocess
    ffmpeg_ok = subprocess.run(["ffmpeg", "-version"], capture_output=True).returncode == 0
    checks.append(("ffmpeg",        ffmpeg_ok,                  "найден" if ffmpeg_ok else "НЕ найден — brew install ffmpeg"))

    # Шрифт
    font_ok = Path(cfg.pdf_font_path).exists()
    checks.append(("Шрифт PDF",     font_ok,                    cfg.pdf_font_path))

    # Output dir
    out_ok = Path(cfg.output_dir).expanduser().exists()
    if not out_ok:
        try:
            Path(cfg.output_dir).expanduser().mkdir(parents=True)
            out_ok = True
        except OSError:
            pass
    checks.append(("Output dir",   out_ok,                     cfg.output_dir))

    for name, ok, value in checks:
        status = "✓" if ok else "✗"
        print(f"  [{status}] {name:<20} {value}")

    if errors:
        print(f"\n  Найдено ошибок: {len(errors)}")
        for e in errors:
            print(f"  ✗ {e}")
        print("\n  Запусти: python run.py --setup")
        sys.exit(1)
    else:
        print("\n  ✓ Конфигурация в порядке. Можно работать!")


def cmd_process_link(url: str, cfg, db, from_start: bool = False):
    """Обрабатывает одну ссылку (Telegram или внешнюю)."""
    import logging
    from app.utils.url_parser import parse_url, ExternalLink
    from app.queue.worker import Worker

    logger = logging.getLogger("tgassistant")

    # Валидация ссылки
    try:
        link = parse_url(url)
    except ValueError as e:
        print(f"\n  ✗ {e}")
        return False

    is_external = isinstance(link, ExternalLink)

    # Idempotency check
    existing = db.get_job_by_url(url)
    if existing and not from_start:
        status = existing["status"]
        if status == "done":
            exports = db.get_exports(existing["id"])
            print(f"\n  Эта ссылка уже обработана!")
            for exp in exports:
                print(f"  → {exp['file_path']}")
            return True
        elif status in ("downloading", "transcribing", "exporting", "collecting", "analyzing", "saving"):
            print(f"\n  Задача уже выполняется (статус: {status}).")
            return False
        elif status == "error":
            print(f"\n  Предыдущая обработка завершилась ошибкой: {existing.get('last_error')}")
            print(f"  Используй: python run.py --retry {existing['id']}")
            return False

    # Создаём задачу
    if existing and from_start:
        job_id = existing["id"]
        db.update_job_status(job_id, "pending")
    elif existing and existing["status"] == "pending":
        job_id = existing["id"]
    else:
        if is_external:
            job_id = db.create_external_job(link)
        else:
            job_id = db.create_job(link)

    worker = Worker(cfg, db)

    if is_external:
        # Внешние ссылки: Telegram client не нужен
        result = worker.process(job_id, link, client=None, from_start=from_start)
    else:
        # Telegram: нужна авторизация
        from app.auth.session_manager import get_authorized_client
        from app.utils.async_utils import run_sync, safe_disconnect, close_loop

        try:
            client = get_authorized_client(cfg)
        except RuntimeError as e:
            print(f"\n  ✗ {e}")
            return False

        try:
            run_sync(client.connect())
            result = worker.process(job_id, link, client, from_start=from_start)
        finally:
            safe_disconnect(client)
            close_loop()

    if result:
        if "collected_dir" in result:
            # Collector результат
            print(f"\n  ✓ Готово! Содержимое собрано:")
            print(f"  → {result['collected_dir']}")
        elif "wiki_dir" in result:
            # Ingest результат
            print(f"\n  ✓ Готово! Сообщение сохранено:")
            print(f"  → {result['wiki_dir']}")
        else:
            # Media результат (PDF)
            print(f"\n  ✓ Готово! PDF сохранены:")
            for label, path in result.items():
                print(f"  → {path}")
        return True
    else:
        job = db.get_job_by_id(job_id)
        print(f"\n  ✗ Обработка не удалась: {job.get('last_error')}")
        print(f"  Подробности: logs/tgassistant.log")
        return False


def cmd_status(db, status_filter=None):
    jobs = db.list_jobs(status_filter)
    if not jobs:
        print("\n  Задач не найдено.")
        return

    print(f"\n  {'ID':<8} {'Статус':<14} {'Создана':<20} {'URL'}")
    print("  " + "─" * 80)
    for job in jobs:
        short_id = job["id"][:8]
        status = job["status"]
        created = job["created_at"][:16] if job["created_at"] else ""
        url = job["url"][:50] + ("..." if len(job["url"]) > 50 else "")
        print(f"  {short_id:<8} {status:<14} {created:<20} {url}")

        if job["status"] == "done":
            exports = db.get_exports(job["id"])
            for exp in exports:
                type_prefixes = {"ingest_wiki": "wiki", "collected": "collected"}
                prefix = type_prefixes.get(exp["export_type"], "pdf")
                print(f"           {'':14} {'':20} → [{prefix}] {exp['file_path']}")
        elif job["status"] == "error":
            err = (job.get("last_error") or "")[:60]
            print(f"           {'':14} {'':20} ✗ {err}")


def cmd_batch(args, cfg, db):
    """Пакетная обработка: парсит заметку, обрабатывает все ссылки, строит индекс."""
    from app.batch.note_parser import parse_note
    from app.batch.batch_runner import BatchRunner

    # Читаем текст
    if args.batch_file:
        path = Path(args.batch_file).expanduser()
        if not path.exists():
            print(f"\n  ✗ Файл не найден: {path}")
            return
        text = path.read_text(encoding="utf-8")
    else:
        text = args.batch_text

    # Парсим заметку
    note = parse_note(text)

    # Переопределяем тему если задано
    if args.topic:
        note.topic = args.topic

    # Вывод сводки
    print(f"\n{'═'*54}")
    print(f"  Тема: {note.topic}")
    print(f"  Найдено URL: {note.total_count} (валидных: {note.valid_count})")
    if note.errors:
        print(f"  Невалидных URL: {len(note.errors)}")
        for line_num, url, err in note.errors:
            print(f"    строка {line_num}: {url}")
    if note.skipped_lines:
        print(f"  Пропущено строк: {len(note.skipped_lines)}")
    print(f"{'═'*54}")

    if note.valid_count == 0:
        print("\n  ✗ Нет валидных ссылок для обработки.")
        return

    # Прогресс в консоль
    def print_progress(job_id, status):
        if status and status.startswith("batch:"):
            print(f"  [{status.replace('batch:', '')}]", end=" ", flush=True)

    # Запускаем батч
    runner = BatchRunner(cfg, db, progress_cb=print_progress)
    result = runner.run(
        note=note,
        from_start=args.from_start,
        use_symlinks=not args.no_symlink,
    )

    # Итоговый отчёт
    print(f"\n\n{'═'*54}")
    print(f"  Результат: {result.succeeded}/{result.total} успешно")
    if result.failed > 0:
        print(f"  Ошибок: {result.failed}")
        for item in result.items:
            if not item.success:
                print(f"    ✗ [{item.index}] {item.entry.url}: {item.error}")
    if result.topic_dir:
        print(f"  Папка темы: {result.topic_dir}")
    print(f"{'═'*54}")


def cmd_retry(job_id: str, cfg, db, from_start: bool):
    import logging
    from app.queue.worker import Worker
    from app.utils.url_parser import parse_url, ExternalLink

    logger = logging.getLogger("tgassistant")

    # Ищем задачу по полному ID или первым 8 символам
    job = db.get_job_by_id(job_id)
    if not job:
        # Попробуем по prefix
        all_jobs = db.list_jobs()
        matches = [j for j in all_jobs if j["id"].startswith(job_id)]
        if len(matches) == 1:
            job = matches[0]
        elif len(matches) > 1:
            print(f"\n  ✗ Неоднозначный ID: {job_id}. Укажи больше символов.")
            return
        else:
            print(f"\n  ✗ Задача не найдена: {job_id}")
            return

    link = parse_url(job["url"])
    is_external = isinstance(link, ExternalLink)
    db.update_job_status(job["id"], "pending", retry_count=0)

    worker = Worker(cfg, db)

    if is_external:
        result = worker.process(job["id"], link, client=None, from_start=from_start)
    else:
        from app.auth.session_manager import get_authorized_client
        from app.utils.async_utils import run_sync, safe_disconnect, close_loop

        try:
            client = get_authorized_client(cfg)
        except RuntimeError as e:
            print(f"\n  ✗ {e}")
            return

        try:
            run_sync(client.connect())
            result = worker.process(job["id"], link, client, from_start=from_start)
        finally:
            safe_disconnect(client)
            close_loop()

    if result:
        print(f"\n  ✓ Повторная обработка успешна!")
        if "collected_dir" in result:
            print(f"  → {result['collected_dir']}")
        elif "wiki_dir" in result:
            print(f"  → {result['wiki_dir']}")
        else:
            for label, path in result.items():
                print(f"  → {path}")
    else:
        print(f"\n  ✗ Повторная обработка не удалась. Смотри: logs/tgassistant.log")


def cmd_cleanup(args, cfg):
    """Удаляет видео/аудио файлы старше N дней в collected/."""
    from app.utils.cleanup import cleanup_media

    days = args.older_than
    dry = args.dry_run
    mode = "DRY RUN" if dry else "LIVE"

    print(f"\n{'═'*54}")
    print(f"  Очистка медиа ({mode})")
    print(f"  Папка: {cfg.output_dir}/collected/")
    print(f"  Старше: {days} дней")
    print(f"{'═'*54}\n")

    result = cleanup_media(cfg.output_dir, older_than_days=days, dry_run=dry)

    mb = result.bytes_freed / 1_048_576
    print(f"\n{'═'*54}")
    if dry:
        print(f"  [DRY RUN] Будет удалено: {result.files_deleted} файлов ({mb:.1f} МБ)")
    else:
        print(f"  Удалено: {result.files_deleted} файлов ({mb:.1f} МБ)")
    if result.files_skipped:
        print(f"  Пропущено (свежие): {result.files_skipped}")
    if result.errors:
        print(f"  Ошибки: {result.errors}")
    print(f"{'═'*54}")


def main():
    _bootstrap()
    parser = _make_parser()
    args = parser.parse_args()

    # --setup не требует загрузки конфига
    if args.setup:
        cmd_setup()
        return

    # --check-config: лёгкая проверка
    if args.check_config:
        cmd_check_config(args)
        return

    # --cleanup: лёгкий режим (только конфиг, без DB и orphan cleanup)
    if args.cleanup:
        from app.config import load_config
        overrides = {}
        if args.output_dir:
            overrides["output_dir"] = args.output_dir
        cfg = load_config(config_file=args.config, overrides=overrides)
        cmd_cleanup(args, cfg)
        return

    # Все остальные команды требуют полного конфига
    try:
        cfg, db = _load_app(args)
    except Exception as e:
        print(f"\n  ✗ Ошибка загрузки конфигурации: {e}")
        print("  Запусти: python run.py --setup")
        sys.exit(1)

    if args.web:
        from app.web.server import start_server
        start_server(cfg, db, host=args.host, port=args.port)
        db.close()
        return

    if args.bot:
        from app.bot.bot import run_bot
        asyncio.run(run_bot(cfg, db))
        db.close()
        return

    if args.batch_file or args.batch_text:
        cmd_batch(args, cfg, db)

    elif args.link:
        for url in args.link:
            print(f"\n{'═'*54}")
            print(f"  Обрабатываю: {url}")
            print(f"{'═'*54}")
            cmd_process_link(url, cfg, db, from_start=args.from_start)

    elif args.watch:
        from app.queue.scheduler import watch_stdin
        watch_stdin(
            lambda url: cmd_process_link(url, cfg, db)
        )

    elif args.status:
        cmd_status(db, status_filter=args.filter)

    elif args.retry:
        cmd_retry(args.retry, cfg, db, from_start=args.from_start)

    db.close()


if __name__ == "__main__":
    main()
