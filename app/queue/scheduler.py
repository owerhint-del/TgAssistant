"""
Scheduler: --watch режим.
Читает ссылки из stdin построчно и передаёт в очередь.
"""
import logging
import sys
from typing import Callable

logger = logging.getLogger("tgassistant.scheduler")


def watch_stdin(process_fn: Callable[[str], None]) -> None:
    """
    --watch режим: читает URL из stdin, одну строку за раз.
    Каждый URL передаётся в process_fn для обработки.
    Выход: Ctrl+C
    """
    print("\n──────────────────────────────────────────────")
    print("  Режим --watch: вставляй ссылки и нажимай Enter")
    print("  Для выхода нажми Ctrl+C")
    print("──────────────────────────────────────────────\n")

    try:
        for line in sys.stdin:
            url = line.strip()
            if not url:
                continue
            if url.lower() in ("exit", "quit", "q"):
                print("Выход из режима watch.")
                break
            print(f"\nОбрабатываю: {url}")
            print("─" * 50)
            try:
                process_fn(url)
            except KeyboardInterrupt:
                raise
            except Exception as e:
                logger.error("Ошибка обработки %s: %s", url, e)
                print(f"\nОшибка: {e}\nПродолжаю ожидание следующей ссылки...\n")
            print("\n──────────────────────────────────────────────")
            print("  Готова к следующей ссылке (или Ctrl+C для выхода)")
            print("──────────────────────────────────────────────\n")

    except KeyboardInterrupt:
        print("\n\nРежим watch остановлен.")
